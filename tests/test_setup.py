"""Setting up a repository: its settings, install and the check of its set-up."""

import dataclasses
import json
import shutil
import tomllib
from pathlib import Path, PurePath, PurePosixPath

import pytest

import issue_assistant as assistant
from support import REF, REPOSITORY, FakeGitHub, make_alert, run_main

SAMPLE = Path(__file__).parent / "sample"


def test_the_settings_are_read_with_defaults(config, root):
    assert config.repository == REPOSITORY
    assert config.engine == "claude" and config.model == ""
    assert config.hosts == {"www.home-assistant.io", "docs.autodarts.io"}
    assert config.area_field == "Area" and config.unmapped_options == ("Other",)
    assert config.notices == ("SUPPORT.md",)
    assert config.asking_kinds() == {"bug", "question", "documentation"}
    assert config.blob == "https://github.com/owner/project/blob"
    assert config.policy == "https://github.com/owner/project/security/policy"
    (root / ".github/issue-assistant/config.toml").unlink()
    plain = assistant.load_config(root, REPOSITORY)
    assert plain.engine == "claude" and plain.hosts == frozenset()
    assert plain.notices == ("SUPPORT.md",)
    assert config.releases is True and plain.releases is True
    for settings in (config, plain):
        assert settings.max_per_day == 20 and settings.max_per_author_per_day == 3
        assert settings.release_replies == 2


def test_the_limits_of_the_ai_runs_can_be_set(root):
    (root / ".github/issue-assistant/config.toml").write_text(
        '[engine]\nname = "claude"\nmax_per_day = 50\n'
        "max_per_author_per_day = 5\nrelease_replies = 0\n",
        encoding="utf-8",
    )
    config = assistant.load_config(root, REPOSITORY)
    assert (config.max_per_day, config.max_per_author_per_day) == (50, 5)
    assert config.release_replies == 0
    assert assistant.check_config(config) == []


def test_the_starter_settings_name_the_default_limits(tmp_path):
    assistant.install(tmp_path, REF, "v1.0.0")
    starter = tomllib.loads(
        (tmp_path / assistant.SETTINGS / "config.toml").read_text("utf-8")
    )
    defaults = assistant.Config(tmp_path, REPOSITORY, (), "")
    assert {key: starter["engine"][key] for key in assistant.LIMITS} == {
        key: getattr(defaults, key) for key in assistant.LIMITS
    }


def test_closing_with_the_release_can_be_switched_off(config, root):
    settings = root / ".github/issue-assistant/config.toml"
    with settings.open("a", encoding="utf-8") as handle:
        handle.write("\n[releases]\nclose_with_release = false\n")
    off = assistant.load_config(root, REPOSITORY)
    assert off.releases is False
    without = tuple(label for label in off.labels if label.name != assistant.FIXED)
    assert assistant.check_labels(dataclasses.replace(off, labels=without)) == []
    assert (
        ".github/labels.toml: the lifecycle label fixed-in-next-release is missing"
        in (assistant.check_labels(dataclasses.replace(config, labels=without)))
    )


def test_settings_without_labels_or_project_are_refused(root):
    (root / ".github/issue-assistant/project.md").unlink()
    with pytest.raises(assistant.AssistantError, match="project.md are needed"):
        assistant.load_config(root, REPOSITORY)


CONFIG = ".github/issue-assistant/config.toml"
LABELS = ".github/labels.toml"
LABEL = (
    '[[label]]\nname = "bug"\ncolor = "d73a4a"\ndescription = "A bug"\ngroup = "type"\n'
)


@pytest.mark.parametrize(
    ("file", "text", "problems"),
    [
        (
            CONFIG,
            'engine = "claude"\n',
            [f"{CONFIG}: engine must be a section, [engine], with its keys below it"],
        ),
        (
            CONFIG,
            '[engine]\nname = 5\nmodel = ["x"]\nmax_per_day = "5"\n'
            "max_per_author_per_day = true\nrelease_replies = 1.5\n",
            [
                f"{CONFIG}: [engine] name must be a string",
                f"{CONFIG}: [engine] model must be a string",
                f"{CONFIG}: [engine] max_per_day must be a whole number",
                f"{CONFIG}: [engine] max_per_author_per_day must be a whole number",
                f"{CONFIG}: [engine] release_replies must be a whole number",
            ],
        ),
        (
            CONFIG,
            '[links]\nhosts = "docs.example.com"\n\n[forms]\narea_field = 3\n'
            'unmapped_options = ["Other", 1]\n\n[transparency]\nfiles = "SUPPORT.md"\n'
            '\n[releases]\nclose_with_release = "no"\n',
            [
                f"{CONFIG}: [links] hosts must be a list of strings",
                f"{CONFIG}: [forms] area_field must be a string",
                f"{CONFIG}: [forms] unmapped_options must be a list of strings",
                f"{CONFIG}: [transparency] files must be a list of strings",
                f"{CONFIG}: [releases] close_with_release must be true or false",
            ],
        ),
        (
            LABELS,
            'label = "bug"\n',
            [
                f"{LABELS}: each label must be a table, [[label]], with its keys below it"
            ],
        ),
        (
            LABELS,
            '[[label]]\nname = "kind/bug"\n\n[[label]]\nname = 5\ncolor = "d73a4a"\n'
            'description = "A bug"\ngroup = "type"\n\n'
            + LABEL
            + 'form_options = "Cards"\nask = "no"\n',
            [
                f"{LABELS}: the label kind/bug needs color, description, group",
                f"{LABELS}: label 2: name must be a string",
                f"{LABELS}: the label bug: form_options must be a list of strings",
                f"{LABELS}: the label bug: ask must be true or false",
            ],
        ),
    ],
)
def test_settings_that_can_t_be_used_stop_with_errors_that_name_them(
    monkeypatch, tmp_path, root, capsys, file, text, problems
):
    (root / file).write_text(text, encoding="utf-8")
    with pytest.raises(assistant.SettingsError) as refused:
        assistant.load_config(root, REPOSITORY)
    assert refused.value.problems == problems
    assert str(refused.value) == "\n".join(problems)
    errors = "".join(f"::error::{problem}\n" for problem in problems)
    code, outputs, _ = run_main(
        monkeypatch, tmp_path, root, None, "engine", READY_ENGINES="claude"
    )
    assert (code, outputs, capsys.readouterr().out) == (1, "", errors)
    # check reads the rest with the defaults, so it names every other problem too.
    code, _, _ = run_main(monkeypatch, tmp_path, root, None, "check")
    assert code == 1 and capsys.readouterr().out.startswith(errors)


@pytest.mark.parametrize(
    ("file", "content", "reason"),
    [
        (CONFIG, b'[engine\nname = "claude"\n', "(at line 1, column "),
        (LABELS, b"[[label]\n", "(at line 1, column "),
        # Saved in Latin-1, as some editors on Windows do.
        (CONFIG, b'[forms]\narea_field = "Bereich f\xfcr"\n', "can't decode byte 0xfc"),
    ],
)
def test_a_file_that_is_no_toml_stops_every_command(
    monkeypatch, tmp_path, root, capsys, file, content, reason
):
    (root / file).write_bytes(content)
    for command in ("check", "engine"):
        code, _, _ = run_main(monkeypatch, tmp_path, root, None, command)
        out = capsys.readouterr().out
        assert code == 1
        assert out.startswith(f"::error::{file} is no valid TOML: ")
        assert reason in out and "Traceback" not in out


def test_unknown_keys_are_named_with_the_key_they_probably_mean(
    monkeypatch, tmp_path, root, capsys
):
    (root / CONFIG).write_text(
        'max_per_day = 5\n\n[engine]\nname = "claude"\nmax_per_dya = 5\n'
        'hosts = ["docs.example.com"]\n\n[release]\nclose_with_release = false\n\n'
        "[links]\nhots = []\n\n[colours]\ndark = true\n",
        encoding="utf-8",
    )
    labels = root / LABELS
    labels.write_text(
        'color = "ffffff"\n\n'
        + labels.read_text("utf-8")
        + '\n[[label]]\nname = "area: sync"\ncolor = "bfdadc"\ndescription = "Sync"\n'
        'group = "area"\nform_option = ["Sync"]\n\n[[labels]]\nname = "other"\n',
        encoding="utf-8",
    )
    problems = [
        f"{LABELS}: color belongs in a [[label]]",
        f"{LABELS}: unknown key labels; did you mean label?",
        f"{LABELS}: the label area: sync: unknown key form_option; "
        "did you mean form_options?",
        f"{CONFIG}: max_per_day belongs in the section [engine]",
        f"{CONFIG}: unknown key max_per_dya in [engine]; did you mean max_per_day?",
        f"{CONFIG}: hosts belongs in the section [links], not in [engine]",
        f"{CONFIG}: unknown section [release]; did you mean [releases]?",
        f"{CONFIG}: unknown key hots in [links]; did you mean hosts?",
        f"{CONFIG}: unknown section [colours]; known: [engine], [links], [forms], "
        "[transparency], [releases]",
    ]
    # The other commands keep the defaults and warn.
    config = assistant.load_config(root, REPOSITORY)
    assert capsys.readouterr().out == "".join(
        f"::warning::{problem}\n" for problem in problems
    )
    assert (config.max_per_day, config.hosts, config.releases) == (
        20,
        frozenset(),
        True,
    )
    assert next(label for label in config.labels if label.name == "area: sync")
    assert config.problems == ()
    # check fails on them.
    checked = assistant.load_config(root, REPOSITORY, lenient=True)
    assert capsys.readouterr().out == ""
    assert assistant.check(checked)[: len(problems)] == problems
    code, _, _ = run_main(monkeypatch, tmp_path, root, None, "check")
    assert code == 1
    assert capsys.readouterr().out.startswith(
        "".join(f"::error::{problem}\n" for problem in problems)
    )


def test_every_key_of_the_settings_is_known():
    assert set(assistant.LABEL_KEYS) == {
        field.name for field in dataclasses.fields(assistant.Label)
    }
    assert (
        tuple(field.name for field in dataclasses.fields(assistant.Acceptance))
        == assistant.ACCEPT_KEYS
    )
    attributes = {field.name for field in dataclasses.fields(assistant.Config)}
    for keys in assistant.CONFIG_KEYS.values():
        for key in keys.values():
            assert key.attribute in attributes


# install


def test_install_writes_the_workflows_with_the_release(sample_root):
    workflow = (sample_root / ".github/workflows/issue-assistant.yml").read_text(
        "utf-8"
    )
    assert f"uses: Dennis-Otto/issue-assistant@{REF} # v1.0.0" in workflow
    assert workflow.count(f"Dennis-Otto/issue-assistant@{REF} # v1.0.0") == 4
    for name in ("issue-lifecycle.yml", "labels.yml"):
        assert (sample_root / ".github/workflows" / name).is_file()
    assert (sample_root / ".github/egress-firewall.yaml").read_text("utf-8") == (
        assistant.TEMPLATES / assistant.FIREWALL
    ).read_text("utf-8")
    assert "/.issue-assistant/" in (sample_root / ".gitignore").read_text("utf-8")


def test_install_and_check_read_the_templates_from_templates_alone(
    monkeypatch, tmp_path
):
    """This repository's own workflows and firewall policy are the blueprint's
    copies of an earlier release, so that a change of a template doesn't fail the
    required check of the issue assistant; install and check never read them."""
    monkeypatch.setattr(assistant, "TOOL", tmp_path / "no-tool-folder")
    root = tmp_path / "project"
    shutil.copytree(SAMPLE, root)
    assistant.install(root, REF, "v1.0.0")
    for name in assistant.TEMPLATE_WORKFLOWS:
        template = assistant.TEMPLATES / assistant.WORKFLOWS / name
        # Byte for byte the template, with the release in the pin of the action.
        assert (root / assistant.WORKFLOWS / name).read_bytes() == (
            assistant.ACTION_PIN.sub(
                f"{assistant.ACTION}@{REF} # v1.0.0", template.read_text("utf-8")
            ).encode()
        )
    assert (root / assistant.FIREWALL).read_bytes() == (
        assistant.TEMPLATES / assistant.FIREWALL
    ).read_bytes()
    assert assistant.check(assistant.load_config(root, REPOSITORY)) == []


def test_install_keeps_what_the_repository_has(tmp_path):
    (tmp_path / ".github/issue-assistant").mkdir(parents=True)
    (tmp_path / ".github/issue-assistant/project.md").write_text(
        "Mine", encoding="utf-8"
    )
    (tmp_path / ".github/egress-firewall.yaml").write_text(
        "mode: enforce\n", encoding="utf-8"
    )
    (tmp_path / ".gitignore").write_text(
        "/build/\n/.issue-assistant/\n", encoding="utf-8"
    )
    done = assistant.install(tmp_path, REF, "v1.0.0")
    assert (tmp_path / ".github/issue-assistant/project.md").read_text(
        "utf-8"
    ) == "Mine"
    assert (
        "kept .github/egress-firewall.yaml; check that it allows the template's hosts"
        in done
    )
    assert "wrote .github/labels.toml; adapt it" in done
    assert "wrote .github/issue-assistant/config.toml; adapt it" in done
    assert (tmp_path / ".gitignore").read_text(
        "utf-8"
    ) == "/build/\n/.issue-assistant/\n"
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    assistant.install(fresh, REF, "v1.0.0")
    assert (fresh / ".gitignore").read_text("utf-8") == "/.issue-assistant/\n"


def test_install_needs_a_commit_hash(tmp_path):
    with pytest.raises(assistant.AssistantError, match="commit hash"):
        assistant.install(tmp_path, "v1.0.0", "v1.0.0")


def test_the_starter_files_make_a_valid_set_up(tmp_path):
    assistant.install(tmp_path, REF, "v1.0.0")
    (tmp_path / "SUPPORT.md").write_text(
        "Claude, an AI by Anthropic.", encoding="utf-8"
    )
    project = tmp_path / assistant.SETTINGS / "project.md"
    config = assistant.load_config(tmp_path, REPOSITORY, lenient=True)
    # The one thing to write by hand: what the project is.
    assert assistant.check(config) == [
        ".github/issue-assistant/project.md: replace the starter's first line with "
        "what the project does and for whom; the AI reads this file before every task"
    ]
    project.write_text(
        project.read_text("utf-8").replace(
            "PROJECT is ONE SENTENCE ON WHAT IT DOES AND FOR WHOM.",
            "Dartboard Cards shows the games of a dartboard on Home Assistant "
            "dashboards, for players who train at home.",
        ),
        encoding="utf-8",
    )
    config = assistant.load_config(tmp_path, REPOSITORY, lenient=True)
    assert assistant.check(config) == []


# check


def test_the_sample_repository_fits(config):
    assert assistant.check(config) == []


def test_label_problems_are_found(config):
    labels = (
        assistant.Label("bug", "D73A4A", "", "type"),
        assistant.Label("bug", "d73a4a", "x" * 101, "kind"),
        assistant.Label("wontfix", "ffffff", "No", "decision", form_options=("Other",)),
    )
    problems = assistant.check_labels(dataclasses.replace(config, labels=labels))
    assert problems[:2] == [
        ".github/labels.toml: the label bug is defined twice",
        ".github/labels.toml: bug needs a color like 'd73a4a'",
    ]
    assert (
        ".github/labels.toml: bug needs a description of 1 to 100 characters"
        in problems
    )
    assert ".github/labels.toml: bug has the unknown group kind" in problems
    assert (
        ".github/labels.toml: only kinds, areas and topics have form_options"
        in problems
    )
    assert ".github/labels.toml: the lifecycle label needs-info is missing" in problems
    without_kinds = dataclasses.replace(
        config, labels=tuple(label for label in config.labels if label.group != "type")
    )
    assert ".github/labels.toml: at least one label needs the group type" in (
        assistant.check_labels(without_kinds)
    )


def test_form_problems_are_found(config, root):
    form = root / ".github/ISSUE_TEMPLATE/bug_report.yml"
    text = form.read_text("utf-8").replace(
        "  - needs-triage", "  - needs-triage\n  - urgent"
    )
    text = text.replace("        - Setup\n", "        - Setup\n        - Online\n")
    text = text.replace("        - X01 game\n", "")
    form.write_text(text, encoding="utf-8")
    labels = config.labels + (
        assistant.Label(
            "area: more", "bfdadc", "More", "area", form_options=("Live card",)
        ),
    )
    problems = assistant.check_forms(dataclasses.replace(config, labels=labels))
    assert problems == [
        ".github/ISSUE_TEMPLATE/bug_report.yml: the label urgent is not in .github/labels.toml",
        ".github/labels.toml: the form option 'Live card' belongs to area: cards, area: more",
        ".github/labels.toml: no label has the form option 'Online'",
        ".github/labels.toml: area: games names the option 'X01 game', which no form has",
    ]


def test_label_names_that_github_takes_for_one_or_refuses_are_found(config):
    """GitHub ignores the case of label names and takes 50 characters at most."""
    labels = (
        *config.labels,
        assistant.Label("Bug", "d73a4a", "A bug", "type"),
        assistant.Label("BUG", "d73a4a", "A bug", "type"),
        assistant.Label("x" * 51, "ffffff", "Long", "topic"),
        assistant.Label("", "ffffff", "Empty", "topic"),
    )
    assert assistant.check_labels(dataclasses.replace(config, labels=labels)) == [
        f"{LABELS}: the labels bug, Bug, BUG differ only in case, which GitHub ignores",
        f"{LABELS}: the name '{'x' * 51}' has 51 characters; GitHub takes 1 to 50",
        f"{LABELS}: the name '' has 0 characters; GitHub takes 1 to 50",
    ]
    assert len(max((label.name for label in config.labels), key=len)) <= 50


def test_the_labels_of_a_form_may_be_one_text_separated_by_commas(config, root):
    """GitHub's syntax for issue forms takes a list or such a text."""
    form = root / ".github/ISSUE_TEMPLATE/bug_report.yml"
    listed = "labels:\n  - bug\n  - needs-triage\n"
    text = form.read_text("utf-8")
    assert listed in text
    form.write_text(text.replace(listed, "labels: bug, needs-triage\n"), "utf-8")
    assert assistant.check_forms(config) == []
    form.write_text(text.replace(listed, "labels: bug,urgent , \n"), "utf-8")
    (root / ".github/ISSUE_TEMPLATE/empty.yml").write_text("", "utf-8")
    assert assistant.check_forms(config) == [
        ".github/ISSUE_TEMPLATE/bug_report.yml: the label urgent is not in "
        ".github/labels.toml",
        ".github/ISSUE_TEMPLATE/empty.yml is no issue form",
    ]


def test_a_multiple_choice_area_fits(config, root):
    form = root / ".github/ISSUE_TEMPLATE/bug_report.yml"
    text = form.read_text("utf-8").replace(
        "      label: Area\n", "      label: Area\n      multiple: true\n"
    )
    form.write_text(text, encoding="utf-8")
    assert assistant.check_forms(config) == []


STARTER = (assistant.TEMPLATES / assistant.SETTINGS / "project.md").read_text("utf-8")


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        (
            STARTER,
            "replace the starter's first line with what the project does and for "
            "whom; the AI reads this file before every task",
        ),
        (
            STARTER.replace("PROJECT", "Dartboard Cards"),
            "replace the starter's first line with what the project does and for "
            "whom; the AI reads this file before every task",
        ),
        (
            "Dart cards.\n",
            "describe the project in at least 80 characters: what it does, for "
            "whom, and where its code and documentation are",
        ),
    ],
)
def test_a_project_description_that_says_too_little_fails_the_check(
    root, text, problem
):
    (root / assistant.SETTINGS / "project.md").write_text(text, encoding="utf-8")
    config = assistant.load_config(root, REPOSITORY)
    assert assistant.check_project(config) == [
        f".github/issue-assistant/project.md: {problem}"
    ]
    assert f".github/issue-assistant/project.md: {problem}" in assistant.check(config)


def test_a_changed_workflow_is_found(config, root):
    path = root / ".github/workflows/issue-assistant.yml"
    path.write_text(
        path.read_text("utf-8").replace("issues: read", "issues: write"),
        encoding="utf-8",
    )
    (root / ".github/workflows/labels.yml").unlink()
    problems = assistant.check_workflows(config)
    assert (
        ".github/workflows/issue-assistant.yml differs from the template" in problems[0]
    )
    assert (
        ".github/workflows/labels.yml is missing; run the install command" in problems
    )


def test_actions_must_be_pinned_to_a_commit(config, root):
    path = root / ".github/workflows/issue-lifecycle.yml"
    path.write_text(path.read_text("utf-8").replace(REF, "v1"), encoding="utf-8")
    problems = assistant.check_workflows(config)
    assert problems == [
        ".github/workflows/issue-lifecycle.yml: pin Dennis-Otto/issue-assistant "
        "to a commit hash, not 'v1'"
    ]


def test_newer_pins_of_the_same_actions_fit(config, root):
    path = root / ".github/workflows/issue-assistant.yml"
    text = path.read_text("utf-8").replace(
        "3d3c42e5aac5ba805825da76410c181273ba90b1", "f" * 40
    )
    path.write_text(text, encoding="utf-8")
    assert assistant.check_workflows(config) == []


def test_firewall_problems_are_found(config, root):
    path = root / ".github/egress-firewall.yaml"
    path.write_text(
        "mode: log\nno-default-urls: true\nallow:\n  - api.github.com\n  - '*.example.com'\n",
        encoding="utf-8",
    )
    problems = assistant.check_workflows(config)
    assert ".github/egress-firewall.yaml: mode must be enforce" in problems
    assert (
        ".github/egress-firewall.yaml: the assistant needs GitHub's default hosts"
        in problems
    )
    assert (
        ".github/egress-firewall.yaml: the engine needs the host api.anthropic.com"
        in problems
    )
    assert (
        ".github/egress-firewall.yaml: name the host *.example.com in full" in problems
    )
    path.unlink()
    assert ".github/egress-firewall.yaml is missing; run the install command" in (
        assistant.check_workflows(config)
    )


def test_actionlint_and_gitignore_are_checked(config, root):
    (root / ".github/actionlint.yaml").write_text(
        "self-hosted-runner:\n  labels: []\n", encoding="utf-8"
    )
    (root / ".gitignore").write_text("/build/\n", encoding="utf-8")
    assert assistant.check_workflows(config) == [
        ".github/actionlint.yaml: list the runner ubuntu-24.04-firewall",
        ".gitignore: add /.issue-assistant/",
    ]
    (root / ".github/actionlint.yaml").write_text(
        "self-hosted-runner:\n  labels: [ubuntu-24.04-firewall]\n", encoding="utf-8"
    )
    (root / ".gitignore").unlink()
    assert assistant.check_workflows(config) == [".gitignore: add /.issue-assistant/"]


def test_config_problems_are_found(config, root):
    broken = dataclasses.replace(
        config,
        engine="unknown",
        hosts=frozenset({"https://evil.example/path"}),
        max_per_day=-1,
        max_per_author_per_day=True,
        release_replies="2",
    )
    assert assistant.check_config(broken) == [
        ".github/issue-assistant/config.toml: unknown engine unknown; known engines: claude",
        ".github/issue-assistant/config.toml: https://evil.example/path is no host name",
        ".github/issue-assistant/config.toml: max_per_day must be a whole number, "
        "0 or more",
        ".github/issue-assistant/config.toml: max_per_author_per_day must be a whole "
        "number, 0 or more",
        ".github/issue-assistant/config.toml: release_replies must be a whole number, "
        "0 or more",
    ]
    (root / "SUPPORT.md").write_text("An AI reads your issue.", encoding="utf-8")
    missing = dataclasses.replace(config, notices=("SUPPORT.md", "docs/missing.md"))
    assert assistant.check_config(missing) == [
        "SUPPORT.md: say that Claude by Anthropic reads the issues",
        "docs/missing.md is missing; it tells reporters who reads their issue",
    ]


def test_the_check_command_reports_problems(monkeypatch, tmp_path, root, capsys):
    code, _, summary = run_main(monkeypatch, tmp_path, root, None, "check")
    assert code == 0 and "Everything fits." in summary
    (root / ".gitignore").unlink()
    code, _, summary = run_main(monkeypatch, tmp_path, root, None, "check")
    assert code == 1
    assert "::error::.gitignore: add /.issue-assistant/" in capsys.readouterr().out


def test_a_model_that_would_add_arguments_fails_the_check_and_the_engine(
    monkeypatch, tmp_path, root, capsys
):
    """The workflow passes the model to the engine's command line without quotes."""
    settings = root / assistant.SETTINGS / "config.toml"
    settings.write_text(
        settings.read_text("utf-8").replace(
            'name = "claude"', 'name = "claude"\nmodel = "x --dangerously"'
        ),
        encoding="utf-8",
    )
    error = (
        "::error::.github/issue-assistant/config.toml: the model 'x --dangerously' "
        "is no model name, such as claude-opus-5-5\n"
    )
    code, _, _ = run_main(monkeypatch, tmp_path, root, None, "check")
    assert code == 1 and capsys.readouterr().out.startswith(error)
    code, outputs, _ = run_main(
        monkeypatch, tmp_path, root, None, "engine", READY_ENGINES="claude"
    )
    assert (code, outputs, capsys.readouterr().out) == (1, "", error)


def test_the_install_command_writes_into_the_workspace(monkeypatch, tmp_path, capsys):
    target = tmp_path / "target"
    target.mkdir()
    code, _, _ = run_main(
        monkeypatch,
        tmp_path,
        target,
        None,
        "install",
        "--ref",
        REF,
        "--version",
        "v1.2.3",
    )
    assert code == 0
    assert "wrote .github/workflows/issue-assistant.yml" in capsys.readouterr().out
    assert "# v1.2.3" in (target / ".github/workflows/labels.yml").read_text("utf-8")
    code, _, _ = run_main(
        monkeypatch,
        tmp_path,
        target,
        None,
        "install",
        "--ref",
        "main",
        "--version",
        "x",
    )
    assert code == 1


def test_yaml_is_read_with_pyyaml(tmp_path):
    path = tmp_path / "data.yml"
    path.write_text("on:\n  push: {}\nlist: [1, 2]\n", encoding="utf-8")
    data = assistant.load_yaml(path)
    assert data == {True: {"push": {}}, "list": [1, 2]}
    problems: list[str] = []
    assert assistant.normalized(data, problems, "data.yml") == {
        "on": {"push": {}},
        "list": [1, 2],
    }
    assert problems == []
    assert json.dumps(assistant.normalized({"uses": f"a/b@{REF}"}, problems, "x")) == (
        '{"uses": "a/b"}'
    )
    assert problems == []


def test_the_tool_files_are_where_the_action_expects_them():
    tool = Path(assistant.__file__).parent
    assert tool == assistant.TOOL
    for name in ("rules.md", "triage.md", "follow-up.md", "maintainer-reply.md"):
        assert (assistant.PROMPTS / name).is_file()
    assert tool / "templates" == assistant.TEMPLATES
    for starter in assistant.STARTERS:
        assert (assistant.TEMPLATES / starter).is_file()
    for name in assistant.TEMPLATE_WORKFLOWS:
        assert (assistant.TEMPLATES / assistant.WORKFLOWS / name).is_file()
    assert (assistant.TEMPLATES / assistant.FIREWALL).is_file()


def test_another_checkout_can_be_checked(monkeypatch, tmp_path, sample_root):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    code, _, summary = run_main(
        monkeypatch,
        tmp_path,
        elsewhere,
        None,
        "check",
        ISSUE_ASSISTANT_ROOT=str(sample_root),
    )
    assert code == 0 and "Everything fits." in summary


@pytest.mark.parametrize("command", ["check", "engine"])
def test_commands_that_read_only_the_checkout_need_no_repository(
    monkeypatch, tmp_path, sample_root, capsys, command
):
    code, _, _ = run_main(
        monkeypatch,
        tmp_path,
        sample_root,
        None,
        command,
        GH_REPO="",
        GITHUB_REPOSITORY="",
    )
    assert code == 0
    assert "::error::" not in capsys.readouterr().out


# The same messages on every system


def test_the_files_of_a_repository_are_named_with_slashes():
    """A Path prints backslashes on Windows; a PurePosixPath prints slashes anywhere."""
    relative = {
        name: value
        for name, value in vars(assistant).items()
        if isinstance(value, PurePath) and not value.is_absolute()
    }
    assert set(relative) >= {
        "LABELS_FILE",
        "SETTINGS",
        "CONTEXT",
        "WORKFLOWS",
        "FIREWALL",
        "ACTIONLINT",
        "FINDINGS_FILE",
    }
    for path in (*relative.values(), *assistant.STARTERS):
        assert type(path) is PurePosixPath, path


def test_messages_name_files_with_slashes(root, config, tmp_path, capsys):
    """Every message that names a file of the repository, on a broken set-up."""
    form = root / ".github/ISSUE_TEMPLATE/bug_report.yml"
    form.write_text(
        form.read_text("utf-8").replace("  - needs-triage", "  - urgent"),
        encoding="utf-8",
    )
    (root / ".github/workflows/labels.yml").unlink()
    (root / ".github/egress-firewall.yaml").write_text("mode: log\n", encoding="utf-8")
    (root / ".github/actionlint.yaml").write_text("{}\n", encoding="utf-8")
    (root / ".gitignore").unlink()
    (root / assistant.FINDINGS_FILE).write_text(
        '[[accept]]\ntool = ""\nrule = ""\nreason = "won\'t fix"\ncomment = "y"\n',
        encoding="utf-8",
    )
    labels = (*config.labels, assistant.Label("bug", "red", "", "kind"))
    messages = assistant.check(
        dataclasses.replace(config, labels=labels, hosts=frozenset({"*"}))
    )
    messages += assistant.check_config(dataclasses.replace(config, engine="unknown"))
    (root / assistant.FINDINGS_FILE).write_text("[[accept]\n", encoding="utf-8")
    messages += assistant.check_accepted_findings(config)
    with pytest.raises(assistant.AssistantError) as refused:
        assistant.load_config(tmp_path, REPOSITORY)
    messages.append(str(refused.value))
    messages += assistant.install(root, REF, "v1.0.0")
    messages += assistant.install(tmp_path / "fresh", REF, "v1.0.0")
    github = FakeGitHub(config)
    github.label_store.append({"name": "custom", "color": "ffffff", "node_id": "L_c"})
    assistant.sync_labels(github, config)
    messages += capsys.readouterr().out.splitlines()
    (root / assistant.FINDINGS_FILE).write_text(
        '[[accept]]\ntool = "Scorecard"\nrule = "CodeReviewID"\n'
        'reason = "won\'t fix"\ncomment = "One maintainer."\n',
        encoding="utf-8",
    )
    github.alert_store = [make_alert(3), make_alert(4, "CodeQL", "js/xss")]
    messages += assistant.watch_findings(github, root)[0]
    messages.append(github.alert_store[0]["dismissed_comment"])

    text = "\n".join(messages)
    for path in (
        ".github/ISSUE_TEMPLATE/bug_report.yml",
        ".github/labels.toml",
        ".github/issue-assistant/project.md",
        ".github/issue-assistant/config.toml",
        ".github/workflows/labels.yml",
        ".github/egress-firewall.yaml",
        ".github/actionlint.yaml",
        ".github/findings.toml",
        "/.issue-assistant/",
    ):
        assert path in text
    assert [message for message in messages if "\\" in message] == []


# Reading YAML without PyYAML


def test_yaml_is_read_with_yq_without_pyyaml(monkeypatch, tmp_path):
    workflow = tmp_path / "workflow.yml"
    workflow.write_text("on: push\n", encoding="utf-8")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return assistant.subprocess.CompletedProcess(command, 0, '{"on": "push"}', "")

    monkeypatch.setitem(assistant.sys.modules, "yaml", None)
    monkeypatch.setattr(assistant.subprocess, "run", run)
    assert assistant.load_yaml(workflow) == {"on": "push"}
    assert calls == [["yq", "-o=json", ".", str(workflow)]]


def test_yaml_without_pyyaml_or_yq_says_what_to_install(monkeypatch, tmp_path):
    def run(command, **kwargs):
        raise FileNotFoundError(command[0])

    monkeypatch.setitem(assistant.sys.modules, "yaml", None)
    monkeypatch.setattr(assistant.subprocess, "run", run)
    with pytest.raises(assistant.AssistantError, match="needs PyYAML or yq"):
        assistant.load_yaml(tmp_path / "workflow.yml")
