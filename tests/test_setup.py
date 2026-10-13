"""Setting up a repository: its settings, install and the check of its set-up."""

import dataclasses
import json
import shutil
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
    config = assistant.load_config(tmp_path, REPOSITORY)
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
        releases="yes",
    )
    assert assistant.check_config(broken) == [
        ".github/issue-assistant/config.toml: unknown engine unknown; known engines: claude",
        ".github/issue-assistant/config.toml: https://evil.example/path is no host name",
        ".github/issue-assistant/config.toml: close_with_release must be true or false",
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
