"""Setting up a repository: its settings, install and the check of its set-up."""

import dataclasses
import io
import json
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path, PurePath, PurePosixPath

import pytest

import issue_assistant as assistant
from support import REF, REPOSITORY, FakeGitHub, make_alert, run_main

SAMPLE = Path(__file__).parent / "sample"
# What check says to do about a workflow or a firewall policy that doesn't fit.
REINSTALL = (
    "write the templates of this release anew with: "
    'dir="$(mktemp -d)" && git clone --quiet --depth 1 --branch '
    f"v{assistant.VERSION_FILE.read_text('utf-8').strip()} "
    'https://github.com/Dennis-Otto/issue-assistant "$dir" && '
    'python3 "$dir/issue_assistant.py" install'
)


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
        assert (settings.max_issues, settings.max_discussions) == (1000, 1000)


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


def test_what_the_ai_reads_of_the_repository_can_be_limited(root):
    (root / ".github/issue-assistant/config.toml").write_text(
        "[context]\nmax_issues = 200\nmax_discussions = 0\n", encoding="utf-8"
    )
    config = assistant.load_config(root, REPOSITORY)
    assert (config.max_issues, config.max_discussions) == (200, 0)
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
    assert {key: starter["context"][key] for key in assistant.CONTEXT_LIMITS} == {
        key: getattr(defaults, key) for key in assistant.CONTEXT_LIMITS
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
            'credit = "no"\n\n[releases]\nclose_with_release = "no"\n',
            [
                f"{CONFIG}: [links] hosts must be a list of strings",
                f"{CONFIG}: [forms] area_field must be a string",
                f"{CONFIG}: [forms] unmapped_options must be a list of strings",
                f"{CONFIG}: [transparency] files must be a list of strings",
                f"{CONFIG}: [transparency] credit must be true or false",
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
            + 'form_options = "Cards"\nask = "no"\nowners = "alice"\nissue_type = 5\n',
            [
                f"{LABELS}: the label kind/bug needs color, description, group",
                f"{LABELS}: label 2: name must be a string",
                f"{LABELS}: the label bug: form_options must be a list of strings",
                f"{LABELS}: the label bug: ask must be true or false",
                f"{LABELS}: the label bug: owners must be a list of strings",
                f"{LABELS}: the label bug: issue_type must be a string",
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


def test_a_project_md_that_is_no_utf_8_stops_every_command(
    monkeypatch, tmp_path, root, capsys
):
    # Saved in Latin-1, with the umlauts of how to address German reporters.
    (root / ".github/issue-assistant/project.md").write_bytes(
        "Ein Projekt für Leser, die Deutsch schreiben.\n".encode("latin-1")
    )
    for command in ("check", "engine"):
        code, _, _ = run_main(monkeypatch, tmp_path, root, None, command)
        out = capsys.readouterr().out
        assert code == 1
        assert out.startswith(
            "::error::.github/issue-assistant/project.md is no UTF-8 text: "
        )
        assert "can't decode byte 0xfc" in out and "Traceback" not in out


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
        f"{CONFIG}: unknown section [colours]; known: [engine], [context], [related], "
        "[discussions], [maintainers], [links], [forms], [transparency], [releases], "
        "[lifecycle], [digest]",
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
    attributes = {field.name for field in dataclasses.fields(assistant.Config)} | {
        f"lifecycle.{field.name}" for field in dataclasses.fields(assistant.Lifecycle)
    }
    for keys in assistant.CONFIG_KEYS.values():
        for key in keys.values():
            assert key.attribute in attributes
    # Each attribute of Config.lifecycle has its key in [lifecycle].
    assert [key.attribute for key in assistant.CONFIG_KEYS["lifecycle"].values()] == [
        f"lifecycle.{field.name}" for field in dataclasses.fields(assistant.Lifecycle)
    ]


# install


def test_install_writes_the_workflows_with_the_release(sample_root):
    workflow = (sample_root / ".github/workflows/issue-assistant.yml").read_text(
        "utf-8"
    )
    # One pin of the release, of the reusable workflow that has the jobs.
    assert workflow.count(f"@{REF} # v1.0.0") == 1
    assert (
        "uses: Dennis-Otto/issue-assistant/.github/workflows/"
        f"reusable-issue-assistant.yml@{REF} # v1.0.0"
    ) in workflow
    for name in ("issue-lifecycle.yml", "labels.yml", "findings.yml"):
        text = (sample_root / ".github/workflows" / name).read_text("utf-8")
        assert f"/reusable-{name}@{REF} # v1.0.0" in text
    # The hosts of the engine of config.toml alone.
    assert (sample_root / ".github/egress-firewall.yaml").read_text("utf-8") == (
        assistant.firewall_policy("claude")
    )
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
    # With every workflow, the one of the discussions too.
    settings = root / assistant.SETTINGS / "config.toml"
    with settings.open("a", encoding="utf-8") as file:
        file.write('\n[discussions]\nanswer_categories = ["Q&A"]\n')
    assistant.install(root, REF, "v1.0.0")
    for name in assistant.TEMPLATE_WORKFLOWS:
        template = assistant.TEMPLATES / assistant.WORKFLOWS / name
        # Byte for byte the template, with the release in the pin of the issue
        # assistant.
        written = (root / assistant.WORKFLOWS / name).read_text("utf-8")
        assert written.encode() == (
            assistant.pinned(template.read_text("utf-8"), REF, "v1.0.0").encode()
        )
        assert written != template.read_text("utf-8")
    assert (root / assistant.FIREWALL).read_bytes() == (
        assistant.firewall_policy("claude").encode()
    )
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
        "kept .github/egress-firewall.yaml; check names each host of the engine it lacks"
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
    done = assistant.install(tmp_path, REF, "v1.0.0", REPOSITORY)
    assert "wrote .github/ISSUE_TEMPLATE/bug_report.yml; adapt it" in done
    assert "wrote .github/ISSUE_TEMPLATE/feature_request.yml; adapt it" in done
    assert (
        "wrote .github/ISSUE_TEMPLATE/config.yml; adapt it, and turn on what its links "
        "need on GitHub"
    ) in done
    assert "added what happens with an issue to SUPPORT.md; adapt it" in done
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


# The release that install writes


def run_git(folder, *args):
    """git in `folder`, with an author and without signing, whatever the computer's
    settings say."""
    result = subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "tag.gpgsign=false",
            *args,
        ],
        cwd=folder,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def release_checkout(folder):
    """A clone of the release v9.8.7, at its annotated tag: the commit and the
    object of the tag, which `git ls-remote` lists first."""
    folder.mkdir(parents=True)
    run_git(folder, "init", "-q", "--template=", "-b", "main")
    run_git(folder, "commit", "-q", "--allow-empty", "-m", "the release")
    run_git(folder, "tag", "-a", "v9.8.7", "-m", "v9.8.7")
    # The major tag, which each release moves, names no version of its own.
    run_git(folder, "tag", "v9")
    return run_git(folder, "rev-parse", "HEAD"), run_git(folder, "rev-parse", "v9.8.7")


def test_install_takes_the_commit_and_the_tag_of_its_checkout(tmp_path):
    tool = tmp_path / "issue-assistant"
    commit, tag_object = release_checkout(tool)
    assert commit != tag_object
    assert assistant.release_of(tool, None, None) == (commit, "v9.8.7")
    assert assistant.release_of(tool, commit, None) == (commit, "v9.8.7")
    assert assistant.release_of(tool, None, "v9.8.7") == (commit, "v9.8.7")
    # A version that is no tag of the checkout, as in the action's smoke test.
    assert assistant.release_of(tool, commit, "smoke-test") == (commit, "smoke-test")


def test_install_refuses_the_version_of_another_release(tmp_path):
    # A clone of a release has no tag of another one, which the pin's comment would
    # name, and Dependabot would take for the version installed.
    tool = tmp_path / "issue-assistant"
    commit, _ = release_checkout(tool)
    for ref in (None, commit):
        with pytest.raises(assistant.AssistantError) as refused:
            assistant.release_of(tool, ref, "v1.0.0")
        assert str(refused.value) == (
            f"The checkout in {tool.as_posix()} is the release v9.8.7, not v1.0.0; "
            "leave out --version, or check out v1.0.0."
        )
    # Off a release, a version of one is taken as it is given.
    run_git(tool, "commit", "-q", "--allow-empty", "-m", "after the release")
    head = run_git(tool, "rev-parse", "HEAD")
    assert assistant.release_of(tool, None, "v1.0.0") == (head, "v1.0.0")


def test_install_refuses_the_object_of_a_tag_for_the_commit(tmp_path):
    tool = tmp_path / "issue-assistant"
    commit, tag_object = release_checkout(tool)
    with pytest.raises(assistant.AssistantError) as refused:
        assistant.release_of(tool, tag_object, "v9.8.7")
    assert str(refused.value) == (
        f"{tag_object} is the object of a tag, not the commit of a release; use "
        f"{commit}, or leave out --ref."
    )


def test_install_refuses_a_commit_or_a_tag_that_isn_t_its_checkout(tmp_path):
    tool = tmp_path / "issue-assistant"
    released, _ = release_checkout(tool)
    run_git(tool, "commit", "-q", "--allow-empty", "-m", "after the release")
    head = run_git(tool, "rev-parse", "HEAD")
    with pytest.raises(assistant.AssistantError) as refused:
        assistant.release_of(tool, released, None)
    assert str(refused.value) == (
        f"The checkout in {tool.as_posix()} is at the commit {head}, not {released}, "
        "and install writes its templates; leave out --ref, or check out the "
        "release of that commit."
    )
    with pytest.raises(assistant.AssistantError) as refused:
        assistant.release_of(tool, None, None)
    assert str(refused.value) == (
        f"The checkout in {tool.as_posix()} is at no release; check out the tag of "
        "one, or clone it: git clone --depth 1 --branch vX.Y.Z "
        "https://github.com/Dennis-Otto/issue-assistant"
    )
    with pytest.raises(assistant.AssistantError) as refused:
        assistant.release_of(tool, None, "v9.8.7")
    assert str(refused.value) == (
        f"The tag v9.8.7 is the commit {released}, not {head} of the checkout in "
        f"{tool.as_posix()}; leave out --version, or check out v9.8.7."
    )


def test_install_without_a_checkout_of_its_own_takes_what_it_is_given(
    monkeypatch, tmp_path
):
    # A folder in the checkout of another repository, such as a copy of the tool
    # in a project, is no checkout of the tool.
    project = tmp_path / "project"
    release_checkout(project)
    tool = project / "tools" / "issue-assistant"
    tool.mkdir(parents=True)
    assert assistant.release_of(tool, REF, "v1.0.0") == (REF, "v1.0.0")
    with pytest.raises(assistant.AssistantError, match="is no git checkout"):
        assistant.release_of(tool, REF, None)
    # Neither is any folder on a computer without git.

    def no_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(assistant.subprocess, "run", no_git)
    with pytest.raises(assistant.AssistantError, match="is no git checkout"):
        assistant.release_of(project, None, "v9.8.7")


# The starters of a set-up


def add_forms(root):
    return assistant.add_forms(root, REPOSITORY, starter_labels=True)


def test_install_writes_the_forms_and_their_chooser_only_without_forms(tmp_path):
    assistant.install(tmp_path, REF, "v1.0.0", REPOSITORY)
    forms = tmp_path / assistant.FORMS
    assert sorted(path.name for path in forms.iterdir()) == [
        "bug_report.yml",
        "config.yml",
        "feature_request.yml",
    ]
    for starter in assistant.FORM_STARTERS.values():
        written = tmp_path / starter
        assert written.read_bytes() == (assistant.TEMPLATES / starter).read_bytes()
    # A repository with forms gets nothing new, not even a chooser that it lacks.
    chooser = forms / "config.yml"
    chooser.unlink()
    assert add_forms(tmp_path) == []
    assert not chooser.exists()
    # The settings of the chooser are no form, and stay what they are.
    for starter in assistant.FORM_STARTERS.values():
        (tmp_path / starter).unlink()
    chooser.write_text("blank_issues_enabled: true\n", encoding="utf-8")
    assert add_forms(tmp_path) == [
        "wrote .github/ISSUE_TEMPLATE/bug_report.yml; adapt it",
        "wrote .github/ISSUE_TEMPLATE/feature_request.yml; adapt it",
    ]
    assert chooser.read_text("utf-8") == "blank_issues_enabled: true\n"
    # So does one under the other name of a YAML file.
    for starter in assistant.FORM_STARTERS.values():
        (tmp_path / starter).unlink()
    chooser.rename(forms / "config.yaml")
    assert len(add_forms(tmp_path)) == 2
    assert not chooser.exists()
    # A template in Markdown is a form.
    for starter in assistant.FORM_STARTERS.values():
        (tmp_path / starter).unlink()
    (forms / "bug.md").write_text("A bug\n", "utf-8")
    assert add_forms(tmp_path) == []
    assert sorted(path.name for path in forms.iterdir()) == ["bug.md", "config.yaml"]


@pytest.mark.parametrize(
    ("settings", "links"),
    [
        (
            None,
            [
                "discussions/categories/q-a",
                "security/advisories/new",
                "blob/HEAD/SUPPORT.md#what-happens-with-your-issue",
            ],
        ),
        (
            '[discussions]\nanswer_categories = ["Help & Support", "Q&A"]\n'
            '[transparency]\nfiles = ["docs/help me.md"]\n',
            [
                "discussions/categories/help-support",
                "security/advisories/new",
                "blob/HEAD/docs/help%20me.md#what-happens-with-your-issue",
            ],
        ),
        (
            "[discussions]\nanswer_categories = []\n[transparency]\nfiles = []\n",
            ["security/advisories/new"],
        ),
    ],
    ids=["starter", "own settings", "no categories and no notice"],
)
def test_the_chooser_links_what_the_repository_has(tmp_path, settings, links):
    if settings:
        file = tmp_path / assistant.SETTINGS / "config.toml"
        file.parent.mkdir(parents=True)
        file.write_text(settings, encoding="utf-8")
    assistant.install(tmp_path, REF, "v1.0.0", REPOSITORY)
    chooser = assistant.load_yaml(tmp_path / assistant.CHOOSER)
    assert chooser["blank_issues_enabled"] is False
    assert [link["url"] for link in chooser["contact_links"]] == [
        f"https://github.com/{REPOSITORY}/{link}" for link in links
    ]
    # Nothing of the template is left, in the links or around them.
    text = (tmp_path / assistant.CHOOSER).read_text("utf-8")
    for placeholder in ("OWNER/NAME", "CATEGORY", "NOTICE_LINK"):
        assert placeholder not in text


def test_check_asks_for_the_repository_in_the_links_of_the_chooser(tmp_path):
    # Without a remote on GitHub, nobody knows the repository yet.
    done = assistant.install(tmp_path, REF, "v1.0.0")
    assert (
        "wrote .github/ISSUE_TEMPLATE/config.yml; replace OWNER/NAME in its links with "
        "the repository, which neither GH_REPO nor the remote origin names"
    ) in done
    chooser = tmp_path / assistant.CHOOSER
    text = chooser.read_text("utf-8")
    assert "https://github.com/OWNER/NAME/security/advisories/new" in text
    assert "https://github.com/OWNER/NAME/blob/HEAD/SUPPORT.md" in text
    config = assistant.load_config(tmp_path, REPOSITORY, lenient=True)
    assert assistant.check_forms(config) == [
        ".github/ISSUE_TEMPLATE/config.yml: replace OWNER/NAME in its links with the "
        "owner and name of the repository"
    ]
    chooser.write_text(text.replace("OWNER/NAME", REPOSITORY), encoding="utf-8")
    assert assistant.check_forms(config) == []


def test_a_chooser_that_is_no_yaml_is_named(config, root):
    # GitHub ignores it without a word, and shows blank issues again.
    (root / assistant.CHOOSER).write_text("contact_links: [\n", "utf-8")
    (problem,) = assistant.check_forms(config)
    assert problem.startswith(".github/ISSUE_TEMPLATE/config.yml is no valid YAML: ")


@pytest.mark.parametrize(
    ("remote", "repository"),
    [
        ("https://github.com/owner/project.git", "owner/project"),
        ("https://github.com/owner/project", "owner/project"),
        ("https://github.com/owner/project.js/", "owner/project.js"),
        ("https://token@github.com/owner/project.git", "owner/project"),
        ("git@github.com:owner/project.git", "owner/project"),
        ("ssh://git@github.com/owner/project.git", "owner/project"),
        ("https://gitlab.com/owner/project.git", ""),
        ("https://github.com.example.com/owner/project", ""),
        ("https://github.com/owner/project/more", ""),
        ("../project", ""),
    ],
)
def test_install_reads_the_repository_of_the_remote(tmp_path, remote, repository):
    run_git(tmp_path, "init", "-q", "--template=", "-b", "main")
    assert assistant.repository_of_remote(tmp_path) == ""
    run_git(tmp_path, "remote", "add", "origin", remote)
    assert assistant.repository_of_remote(tmp_path) == repository


@pytest.mark.parametrize(
    ("env", "named"),
    [
        ({"GH_REPO": "", "GITHUB_REPOSITORY": ""}, "owner/remote"),
        ({"GH_REPO": "owner/given"}, "owner/given"),
        ({"GH_REPO": "", "GITHUB_REPOSITORY": "owner/run"}, "owner/run"),
    ],
    ids=["remote", "GH_REPO", "GITHUB_REPOSITORY"],
)
def test_the_install_command_names_the_repository_in_the_chooser(
    monkeypatch, tmp_path, capsys, env, named
):
    package = tmp_path / "package"
    package.mkdir()
    monkeypatch.setattr(assistant, "TOOL", package)
    monkeypatch.setattr(assistant, "repository_of_remote", lambda root: "owner/remote")
    target = tmp_path / "target"
    target.mkdir()
    argv = ("install", "--ref", REF, "--version", "v1.2.3")
    code, _, _ = run_main(monkeypatch, tmp_path, target, None, *argv, **env)
    assert code == 0, capsys.readouterr().out
    chooser = (target / assistant.CHOOSER).read_text("utf-8")
    assert f"https://github.com/{named}/security/advisories/new" in chooser


def test_install_writes_the_form_only_with_the_starter_labels(tmp_path):
    # The labels of a repository may not map the form's label and areas, and a form
    # would override the templates of its owner's .github repository.
    labels = tmp_path / ".github/labels.toml"
    labels.parent.mkdir(parents=True)
    labels.write_text('[[label]]\nname = "defect"\ncolor = "d73a4a"\n', "utf-8")
    done = assistant.install(tmp_path, REF, "v1.0.0")
    starters = (assistant.TEMPLATES / assistant.FORMS).as_posix()
    assert (
        "wrote no issue forms for your own .github/labels.toml; the starters in "
        f"{starters} need its labels and areas there"
    ) in done
    assert not (tmp_path / ".github/ISSUE_TEMPLATE").exists()
    assert labels.read_text("utf-8").startswith('[[label]]\nname = "defect"')
    # A fresh set-up writes both.
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    done = assistant.install(fresh, REF, "v1.0.0")
    assert "wrote .github/ISSUE_TEMPLATE/bug_report.yml; adapt it" in done


@pytest.mark.parametrize(
    ("kind", "label"), [("bug", "bug"), ("feature", "enhancement")]
)
def test_the_starter_forms_choose_the_starter_area_labels(kind, label):
    form = assistant.load_yaml(assistant.TEMPLATES / assistant.FORM_STARTERS[kind])
    assert form["labels"] == [label, assistant.NEEDS_TRIAGE]
    labels = assistant.load_labels(
        assistant.TEMPLATES / assistant.LABELS_FILE, assistant.Problems()
    )
    (area,) = [item for item in form["body"] if item.get("id") == "area"]
    assert area["attributes"]["label"] == assistant.Config.area_field
    chosen = {option for label in labels for option in label.form_options}
    options = set(area["attributes"]["options"])
    assert options == chosen | set(assistant.Config.unmapped_options)
    assert set(form["labels"]) <= {label.name for label in labels}
    # It tells reporters that an AI answers first, and where they read more.
    intro = form["body"][0]["attributes"]["value"]
    assert "AI issue assistant" in intro and "SUPPORT.md" in intro


def test_install_tells_reporters_what_happens_with_their_issue(tmp_path, root):
    # Without SUPPORT.md, install writes one.
    assistant.install(tmp_path, REF, "v1.0.0")
    support = (tmp_path / "SUPPORT.md").read_text("utf-8")
    assert support.startswith("# Support\n\n## What happens with your issue\n\n")
    assert "Claude, an AI by Anthropic" in support
    assert "fixed-in-next-release" in support and "{" not in support
    assert assistant.check_config(assistant.load_config(tmp_path, REPOSITORY)) == []
    # A file that names the engine stays as it is; one that doesn't gets the section
    # below its text, in German for a German file.
    assert assistant.add_notices(tmp_path) == []
    settings = root / assistant.SETTINGS / "config.toml"
    settings.write_text(
        settings.read_text("utf-8").replace(
            'files = ["SUPPORT.md"]',
            'files = ["SUPPORT.md", "docs/help.md", "SUPPORT.de.md"]\n\n'
            "[releases]\nclose_with_release = false",
        ),
        encoding="utf-8",
    )
    (root / "docs/help.md").write_text("# Help\n\nAsk away.\n\n\n", encoding="utf-8")
    kept = (root / "SUPPORT.md").read_text("utf-8")
    assert assistant.add_notices(root) == [
        "added what happens with an issue to docs/help.md; adapt it",
        "added what happens with an issue to SUPPORT.de.md; adapt it",
    ]
    assert (root / "SUPPORT.md").read_text("utf-8") == kept
    help_page = (root / "docs/help.md").read_text("utf-8")
    assert help_page.startswith(
        "# Help\n\nAsk away.\n\n## What happens with your issue"
    )
    # Without releases, GitHub closes a fixed issue with the merge.
    assert "fixed-in-next-release" not in help_page
    german = (root / "SUPPORT.de.md").read_text("utf-8")
    assert "## Was mit deinem Issue geschieht" in german
    assert "Claude, eine KI von Anthropic" in german
    assert assistant.check_config(assistant.load_config(root, REPOSITORY)) == []


def test_install_writes_no_notice_outside_the_repository_or_for_an_unknown_engine(
    tmp_path,
):
    root = tmp_path / "project"
    assistant.install(root, REF, "v1.0.0")
    settings = root / assistant.SETTINGS / "config.toml"
    settings.write_text(
        settings.read_text("utf-8").replace(
            'files = ["SUPPORT.md"]', 'files = ["../SUPPORT.md"]'
        ),
        encoding="utf-8",
    )
    assert assistant.add_notices(root) == [
        "kept ../SUPPORT.md, which is outside the repository"
    ]
    assert not (tmp_path / "SUPPORT.md").exists()
    settings.write_text(
        settings.read_text("utf-8").replace('name = "claude"', 'name = "unknown"'),
        encoding="utf-8",
    )
    assert assistant.add_notices(root) == []


def test_install_writes_no_notice_with_periods_that_the_sweep_cannot_keep(tmp_path):
    root = tmp_path / "project"
    assistant.install(root, REF, "v1.0.0")
    (root / "SUPPORT.md").unlink(missing_ok=True)
    settings = root / assistant.SETTINGS / "config.toml"
    settings.write_text(
        settings.read_text("utf-8").replace(
            "lock_closed_after_days = 0", "lock_closed_after_days = 5"
        ),
        encoding="utf-8",
    )
    assert assistant.add_notices(root) == []
    assert not (root / "SUPPORT.md").exists()


@pytest.mark.parametrize(
    ("language", "phrases", "lock"),
    [
        (
            "en",
            (
                "a reminder follows after 20 days and the issue closes after 45 days;",
                "closes 7 days later,",
                "write a comment within 60 days and",
            ),
            "closed for more than 90 days.",
        ),
        (
            "de",
            (
                "Ohne Antwort folgt nach 20 Tagen eine Erinnerung, nach 45 Tagen wird",
                "wird nach 7 Tagen geschlossen,",
                "schreib innerhalb von 60 Tagen einen Kommentar,",
            ),
            "seit mehr als 90 Tagen geschlossen sind.",
        ),
    ],
)
def test_the_notice_says_when_the_assistant_acts(config, language, phrases, lock):
    engine = assistant.ENGINES["claude"]
    lifecycle = assistant.Lifecycle(
        remind_after_days=20,
        close_after_days=45,
        duplicate_grace_days=7,
        reopen_after_release_days=60,
    )
    text = assistant.notice(
        dataclasses.replace(config, lifecycle=lifecycle), engine, language
    )
    for phrase in phrases:
        assert phrase in text, phrase
    # Locking is off: no line on it.
    assert lock not in text and "{" not in text
    locking = dataclasses.replace(lifecycle, lock_closed_after_days=90)
    text = assistant.notice(
        dataclasses.replace(config, lifecycle=locking), engine, language
    )
    assert lock in text and "{" not in text
    # The engine and its vendor come from ENGINES; the fix of an issue is one line,
    # which a repository without releases leaves out.
    assert f"{engine.name}, " in text and engine.vendor in text
    assert sum(assistant.FIXED in line for line in text.splitlines()) == 1


# actionlint's settings


ACTIONLINT_EDITS = [
    # The settings of this repository's blueprint, with a comment.
    (
        "self-hosted-runner:\n  labels:\n    # The preview.\n    - other\n",
        "self-hosted-runner:\n  labels:\n    - ubuntu-24.04-firewall\n"
        "    # The preview.\n    - other\n",
    ),
    (
        "self-hosted-runner:\n  labels: []\n",
        "self-hosted-runner:\n  labels: [ubuntu-24.04-firewall]\n",
    ),
    (
        "self-hosted-runner:\n  labels: [a, b] # two\n",
        "self-hosted-runner:\n  labels: [ubuntu-24.04-firewall, a, b] # two\n",
    ),
    (
        "self-hosted-runner:\n",
        "self-hosted-runner:\n  labels:\n    - ubuntu-24.04-firewall\n",
    ),
    (
        "'self-hosted-runner':\n    labels:\n        - a",
        "'self-hosted-runner':\n    labels:\n        - ubuntu-24.04-firewall\n"
        "        - a\n",
    ),
    ("", "self-hosted-runner:\n  labels:\n    - ubuntu-24.04-firewall\n"),
    (
        "# Settings\nconfig-variables: null\n",
        "# Settings\nconfig-variables: null\nself-hosted-runner:\n  labels:\n"
        "    - ubuntu-24.04-firewall\n",
    ),
    # Items at the indentation of their key, as many formatters write them.
    (
        "self-hosted-runner:\n  labels:\n  # The preview.\n  - a\nother: 1\n",
        "self-hosted-runner:\n  labels:\n  - ubuntu-24.04-firewall\n"
        "  # The preview.\n  - a\nother: 1\n",
    ),
    (
        "self-hosted-runner:\nlabels:\n- a\n",
        "self-hosted-runner:\n  labels:\n    - ubuntu-24.04-firewall\nlabels:\n- a\n",
    ),
]


@pytest.mark.parametrize(("text", "edited"), ACTIONLINT_EDITS)
def test_install_lists_the_runner_in_actionlint_s_settings(text, edited):
    keys = ("self-hosted-runner", "labels")
    assert assistant.with_item(text, keys, assistant.FIREWALL_RUNNER) == edited


@pytest.mark.parametrize(
    "text",
    [
        # A list of the top level.
        "- a\n",
        # Something else where a mapping or a list belongs.
        "self-hosted-runner: 3\n",
        "self-hosted-runner:\n  labels: a\n",
        "self-hosted-runner: {labels: [a]}\n",
    ],
)
def test_install_leaves_actionlint_settings_that_it_can_t_edit_safely(text):
    keys = ("self-hosted-runner", "labels")
    assert assistant.with_item(text, keys, assistant.FIREWALL_RUNNER) is None


def test_install_lets_actionlint_know_the_queue(root):
    """The runner of the AI is in the reusable workflow, which actionlint doesn't
    read in a repository that calls it; the queue is in the template."""
    path = root / assistant.ACTIONLINT
    assert assistant.add_to_actionlint(root) == []
    path.write_text(
        "paths:\n  .github/workflows/ci.yml:\n    ignore:\n      - 'x'\n",
        encoding="utf-8",
    )
    assert assistant.add_to_actionlint(root) == [
        "let actionlint ignore the queue of issue-assistant.yml in "
        ".github/actionlint.yaml",
    ]
    assert path.read_text("utf-8") == (
        "paths:\n"
        "  .github/workflows/issue-assistant.yml:\n"
        "    ignore:\n"
        '      - \'unexpected key "queue" for "concurrency" section\'\n'
        "  .github/workflows/ci.yml:\n    ignore:\n      - 'x'\n"
    )
    assert assistant.add_to_actionlint(root) == []
    assert assistant.check_workflows(assistant.load_config(root, REPOSITORY)) == []
    path.write_text("[a]\n", encoding="utf-8")
    assert assistant.add_to_actionlint(root) == [
        '.github/actionlint.yaml: add \'unexpected key "queue" for "concurrency" '
        "section' to the list paths > .github/workflows/issue-assistant.yml > ignore; "
        "its YAML is too unusual to edit safely",
    ]
    assert path.read_text("utf-8") == "[a]\n"


def test_install_writes_the_rest_without_reading_actionlint_s_settings(
    monkeypatch, root
):
    # Settings that are no YAML, which check reports.
    path = root / assistant.ACTIONLINT
    path.write_text("self-hosted-runner: [a\n", encoding="utf-8")
    (done,) = assistant.add_to_actionlint(root)
    assert done.startswith(
        '.github/actionlint.yaml: add \'unexpected key "queue" for "concurrency" '
        "section' to the list paths > .github/workflows/issue-assistant.yml > "
        "ignore; .github/actionlint.yaml is no valid YAML: "
    )
    # A computer with neither PyYAML nor yq.
    path.write_text("self-hosted-runner:\n  labels: []\n", encoding="utf-8")
    monkeypatch.setitem(sys.modules, "yaml", None)
    real_run = subprocess.run

    def without_yq(command, *args, **kwargs):
        if command[0] == "yq":
            raise FileNotFoundError("yq")
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(assistant.subprocess, "run", without_yq)
    done = assistant.install(root, REF, "v1.0.0")
    assert (
        '.github/actionlint.yaml: add \'unexpected key "queue" for "concurrency" '
        "section' to the list paths > .github/workflows/issue-assistant.yml > "
        "ignore; Reading the workflows needs PyYAML or yq; install one of them, "
        "for example with: pip install pyyaml"
    ) in done
    assert "wrote .github/workflows/issue-assistant.yml" in done
    assert path.read_text("utf-8") == "self-hosted-runner:\n  labels: []\n"


# Setting up GitHub


class FakeEnvironments(assistant.GitHub):
    """The calls of `install --setup` to GitHub, in memory."""

    def __init__(self, environment=None, policies=(), secrets=()):
        super().__init__(REPOSITORY)
        self.environment = environment
        self.policies = [{"name": name, "type": "branch"} for name in policies]
        self.secrets = [{"name": name} for name in secrets]
        self.calls = []
        self.error = None

    def _gh(self, args, data=None, *, read=True):
        method = args[args.index("--method") + 1] if "--method" in args else "GET"
        path = args[1].removeprefix(f"repos/{REPOSITORY}")
        self.calls.append((method, path, data))
        environment = "/environments/issue-assistant"
        if path == "":
            return {"default_branch": "trunk"}
        if (method, path) == ("PUT", environment):
            self.environment = {"name": "issue-assistant", **data}
            return self.environment
        if path == environment:
            if self.error:
                raise self.error
            if self.environment is None:
                raise assistant.GitHubError("gh api: HTTP 404: Not Found", status=404)
            return self.environment
        if path == f"{environment}/deployment-branch-policies?per_page=100":
            return {"total_count": len(self.policies), "branch_policies": self.policies}
        if (method, path) == ("POST", f"{environment}/deployment-branch-policies"):
            self.policies.append(data)
            return data
        assert path == f"{environment}/secrets?per_page=100"
        return {"total_count": len(self.secrets), "secrets": self.secrets}


AUTO_CLOSE = (
    "left for you: clear Settings > General > Issues > Auto-close issues with "
    "merged linked pull requests on https://github.com/owner/project/settings, so "
    "that a fixed issue stays open until its release"
)


def test_setup_creates_the_environment_for_the_default_branch_only(config):
    github = FakeEnvironments()
    assert list(assistant.set_up_github(github, config, ask=False)) == [
        "created the environment issue-assistant",
        "let the branch trunk use the environment issue-assistant",
        "left for you: gh secret set CLAUDE_CODE_OAUTH_TOKEN --env issue-assistant "
        "--repo owner/project, or set ANTHROPIC_API_KEY instead",
        AUTO_CLOSE,
    ]
    assert github.calls[2] == (
        "PUT",
        "/environments/issue-assistant",
        {
            "deployment_branch_policy": {
                "protected_branches": False,
                "custom_branch_policies": True,
            }
        },
    )
    assert github.policies == [{"name": "trunk", "type": "branch"}]
    # Run again, it changes nothing, and finds the secret that was set meanwhile.
    github.secrets = [{"name": "ANTHROPIC_API_KEY"}]
    github.calls = []
    without_releases = dataclasses.replace(config, releases=False)
    assert list(assistant.set_up_github(github, without_releases, ask=False)) == [
        "kept the environment issue-assistant, for the branch trunk",
        "kept the secret of Claude in the environment issue-assistant",
    ]
    assert [call[0] for call in github.calls] == ["GET"] * 4


@pytest.mark.parametrize(
    ("policy", "who"),
    [
        (None, "every branch"),
        (
            {"protected_branches": True, "custom_branch_policies": False},
            "protected branches",
        ),
    ],
)
def test_setup_keeps_an_environment_that_a_maintainer_set_up_otherwise(
    config, policy, who
):
    github = FakeEnvironments({"deployment_branch_policy": policy}, secrets=["X"])
    lines = list(assistant.set_up_github(github, config, ask=False))
    assert lines[0] == (
        f"kept the environment issue-assistant, which {who} may use; limit it to "
        "trunk under Settings > Environments"
    )
    assert not [call for call in github.calls if call[0] != "GET"]


def test_setup_asks_for_the_secret_in_a_terminal(monkeypatch, config):
    asked = []

    def gh(args, **kwargs):
        asked.append(args)
        return subprocess.CompletedProcess(args, len(asked) - 1)

    monkeypatch.setattr(assistant.subprocess, "run", gh)
    environment = {"deployment_branch_policy": {"custom_branch_policies": True}}
    github = FakeEnvironments(environment, policies=["trunk"])
    lines = list(assistant.set_up_github(github, config, ask=True))
    command = (
        "gh secret set CLAUDE_CODE_OAUTH_TOKEN --env issue-assistant "
        "--repo owner/project"
    )
    assert lines[1:3] == [
        f"{command}: paste the secret of Claude, or ANTHROPIC_API_KEY later",
        "set the secret CLAUDE_CODE_OAUTH_TOKEN of the environment issue-assistant",
    ]
    # gh reads the secret from the terminal; it never passes through the script.
    assert asked == [command.split()]
    with pytest.raises(assistant.AssistantError) as refused:
        list(assistant.set_up_github(github, config, ask=True))
    assert str(refused.value) == f"gh set no secret; run {command}"


@pytest.mark.parametrize(
    ("engine", "secret"),
    [
        ("codex", "OPENAI_API_KEY"),
        ("gemini", "GEMINI_API_KEY"),
        ("copilot", "COPILOT_GITHUB_TOKEN"),
    ],
)
def test_setup_asks_for_the_one_secret_of_another_engine(
    monkeypatch, config, engine, secret
):
    chosen = dataclasses.replace(config, engine=engine, releases=False)
    environment = {"deployment_branch_policy": {"custom_branch_policies": True}}
    command = f"gh secret set {secret} --env issue-assistant --repo owner/project"
    github = FakeEnvironments(environment, policies=["trunk"])
    assert list(assistant.set_up_github(github, chosen, ask=False))[1:] == [
        f"left for you: {command}"
    ]
    asked = []

    def gh(args, **kwargs):
        asked.append(args)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(assistant.subprocess, "run", gh)
    name = assistant.ENGINES[engine].name
    assert list(assistant.set_up_github(github, chosen, ask=True))[1:] == [
        f"{command}: paste the secret of {name}",
        f"set the secret {secret} of the environment issue-assistant",
    ]
    assert asked == [command.split()]
    github.secrets = [{"name": secret}]
    assert list(assistant.set_up_github(github, chosen, ask=False))[1:] == [
        f"kept the secret of {name} in the environment issue-assistant"
    ]


def test_setup_stops_when_github_fails(config):
    github = FakeEnvironments()
    github.error = assistant.GitHubError("gh api: HTTP 403: Forbidden", status=403)
    with pytest.raises(assistant.GitHubError):
        list(assistant.set_up_github(github, config, ask=False))
    assert [call[0] for call in github.calls] == ["GET", "GET"]


def test_the_install_command_sets_up_github(monkeypatch, tmp_path, capsys):
    package = tmp_path / "package"
    package.mkdir()
    monkeypatch.setattr(assistant, "TOOL", package)
    monkeypatch.setattr(assistant.shutil, "which", lambda name: f"/bin/{name}")
    # No terminal: gh can't ask for the secret, and install says how to set it.
    monkeypatch.setattr(sys, "stdin", io.StringIO())
    github = FakeEnvironments()
    target = tmp_path / "target"
    target.mkdir()
    argv = ("install", "--ref", REF, "--version", "v1.2.3", "--setup")
    code, _, _ = run_main(monkeypatch, tmp_path, target, github, *argv)
    out = capsys.readouterr().out.splitlines()
    assert code == 0, out
    assert out[out.index("set up owner/project on GitHub:") + 1] == (
        "created the environment issue-assistant"
    )
    assert out[-2] == AUTO_CLOSE
    # Without gh, nothing happens on GitHub.
    monkeypatch.setattr(assistant.shutil, "which", lambda name: None)
    code, _, _ = run_main(monkeypatch, tmp_path, target, github, *argv)
    assert code == 1
    assert capsys.readouterr().out.splitlines()[-1] == (
        "::error::--setup needs the GitHub CLI, gh: https://cli.github.com"
    )


def test_setup_finds_the_repository_of_the_checkout(monkeypatch, tmp_path):
    answers = iter([("owner/project\n", "", 0), ("", "no git remotes found", 1)])

    def gh(args, **kwargs):
        assert args[:3] == ["gh", "repo", "view"] and kwargs["cwd"] == tmp_path
        stdout, stderr, code = next(answers)
        return subprocess.CompletedProcess(args, code, stdout, stderr)

    monkeypatch.setattr(assistant.subprocess, "run", gh)
    assert assistant.repository_of_checkout(tmp_path) == "owner/project"
    with pytest.raises(assistant.AssistantError) as refused:
        assistant.repository_of_checkout(tmp_path)
    assert str(refused.value) == (
        f"gh finds no repository on GitHub for {tmp_path.as_posix()} (no git remotes "
        "found); name it with GH_REPO=owner/name."
    )


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


def test_owners_issue_types_and_priorities_are_checked(config):
    crowd = tuple(f"dev{number}" for number in range(9))
    labels = (
        *config.labels,
        assistant.Label(
            "area: api", "c5def5", "The API", "area", owners=("@alice", "a_b", *crowd)
        ),
        assistant.Label("area: web", "c5def5", "The web", "area", issue_type="Bug"),
        assistant.Label("a11y", "c5def5", "Accessible", "topic", owners=("alice",)),
        assistant.Label("chore", "a2eeef", "A chore", "type", issue_type=" Task"),
        assistant.Label("none", "e4e4e4", "Not urgent", "priority"),
    )
    problems = assistant.check_labels(dataclasses.replace(config, labels=labels))
    assert problems == [
        f"{LABELS}: write the owner @alice of area: api without @",
        f"{LABELS}: the owner 'a_b' of area: api is no login of GitHub",
        f"{LABELS}: area: api has 11 owners; GitHub assigns at most 10 people to "
        "an issue",
        f"{LABELS}: only kinds have an issue_type, not area: web",
        f"{LABELS}: only areas have owners, not a11y",
        f"{LABELS}: the issue_type of chore starts or ends with a space",
        f"{LABELS}: no priority can be named none, the answer of the AI for an issue "
        "without one",
    ]


def test_the_examples_of_the_starter_labels_work_when_uncommented(tmp_path):
    """The starter shows priorities, owners and issue types in comments; taken in,
    they pass the checks."""
    starter = (assistant.TEMPLATES / assistant.LABELS_FILE).read_text("utf-8")
    # A commented line of TOML: a table or a key with its value, and nothing after.
    toml = r'^# (\[\[label\]\]|[a-z_]+ = (?:"[^"]*"|\[[^]]*\]|true|false))$'
    uncommented, count = re.subn(toml, r"\1", starter, flags=re.MULTILINE)
    assert count > 10
    path = tmp_path / "labels.toml"
    path.write_text(uncommented, encoding="utf-8")
    found = assistant.Problems()
    labels = assistant.load_labels(path, found)
    assert found == assistant.Problems()
    config = assistant.Config(tmp_path, REPOSITORY, labels, "")
    assert assistant.check_labels(config) == []
    assert config.group("priority")
    assert any(label.owners for label in labels)
    assert any(label.issue_type for label in labels)


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


def test_a_form_that_is_no_yaml_is_named_with_the_other_problems(config, root):
    form = root / ".github/ISSUE_TEMPLATE/bug_report.yml"
    form.write_text("name: Bug\nlabels: [bug\n", "utf-8")
    (root / ".github/ISSUE_TEMPLATE/latin.yml").write_bytes(b"name: F\xfcr\n")
    problems = assistant.check_forms(config)
    assert problems[0].startswith(
        ".github/ISSUE_TEMPLATE/bug_report.yml is no valid YAML: "
    )
    assert problems[1].startswith(".github/ISSUE_TEMPLATE/latin.yml is no valid YAML: ")
    assert "can't decode byte 0xfc" in problems[1]


def test_a_workflow_that_is_no_yaml_stops_the_check_with_one_error(
    monkeypatch, tmp_path, root, capsys
):
    (root / ".github/workflows/issue-assistant.yml").write_text("on: [push\n", "utf-8")
    code, _, _ = run_main(monkeypatch, tmp_path, root, None, "check")
    out = capsys.readouterr().out
    assert code == 1 and "Traceback" not in out
    assert "::error::.github/workflows/issue-assistant.yml is no valid YAML: " in out


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
        path.read_text("utf-8").replace("discussions: read", "discussions: write"),
        encoding="utf-8",
    )
    (root / ".github/workflows/labels.yml").unlink()
    problems = assistant.check_workflows(config)
    assert problems == [
        ".github/workflows/issue-assistant.yml differs from the template of the issue "
        f"assistant, and only the refs of its actions may differ; {REINSTALL}",
        f".github/workflows/labels.yml is missing; {REINSTALL}",
    ]


def test_actions_must_be_pinned_to_a_commit(config, root):
    path = root / ".github/workflows/issue-lifecycle.yml"
    path.write_text(path.read_text("utf-8").replace(REF, "v1"), encoding="utf-8")
    problems = assistant.check_workflows(config)
    assert problems == [
        ".github/workflows/issue-lifecycle.yml: pin Dennis-Otto/issue-assistant/"
        ".github/workflows/reusable-issue-lifecycle.yml to a commit hash, not 'v1'"
    ]


def test_newer_pins_of_the_same_actions_fit(config, root):
    """A move of the pins, as Renovate or Dependabot makes it, is all an update of
    the issue assistant needs, when its release changed no template."""
    # The workflow of the discussions too, which the sample doesn't need.
    discussions = (
        assistant.TEMPLATES / assistant.WORKFLOWS / assistant.DISCUSSION_WORKFLOW
    )
    (root / ".github/workflows" / assistant.DISCUSSION_WORKFLOW).write_text(
        assistant.pinned(discussions.read_text("utf-8"), REF, "v1.0.0"),
        encoding="utf-8",
    )
    for name in assistant.TEMPLATE_WORKFLOWS:
        path = root / ".github/workflows" / name
        text = path.read_text("utf-8").replace(f"{REF} # v1.0.0", f"{'f' * 40} # v9")
        assert text != path.read_text("utf-8")
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
    assert f".github/egress-firewall.yaml is missing; {REINSTALL}" in (
        assistant.check_workflows(config)
    )


# The runner that a repository may name for the AI in place of GitHub's
# egress-firewall runner, behind Harden-Runner.
HARDENED = "ubuntu-24.04"
FIREWALL_PATH = ".github/egress-firewall.yaml"


def endpoints_of(outputs):
    """The endpoints that the command endpoints wrote, as (host, port)."""
    (line,) = outputs.splitlines()
    name, _, value = line.partition("=")
    assert name == "endpoints"
    pairs = [endpoint.rpartition(":") for endpoint in value.split(" ")]
    return [(host, int(port)) for host, _, port in pairs]


@pytest.mark.parametrize("engine", list(assistant.ENGINES))
def test_harden_runner_gets_the_hosts_of_the_firewall_policy(
    monkeypatch, tmp_path, root, engine, capsys
):
    """On the runner of ISSUE_ASSISTANT_AI_RUNNER, Harden-Runner lets the job
    analyze reach what the firewall lets its runner reach: the hosts of the policy
    and GitHub's own, which the firewall allows without the policy, as its header
    says. The endpoints come from the policy itself, so the two can't drift apart."""
    policy = assistant.firewall_policy(engine)
    (root / FIREWALL_PATH).write_text(policy, encoding="utf-8")
    code, outputs, _ = run_main(
        monkeypatch,
        tmp_path,
        root,
        None,
        "endpoints",
        ISSUE_ASSISTANT_AI_RUNNER=HARDENED,
    )
    assert code == 0
    endpoints = endpoints_of(outputs)
    hosts = assistant.allowed_hosts(policy) | set(assistant.FIREWALL_DEFAULT_HOSTS)
    # Each host on HTTPS and on HTTP, as the firewall allows it on each port.
    assert sorted(endpoints) == sorted(
        (host, port) for host in hosts for port in (443, 80)
    )
    header = policy.partition("\nallow:\n")[0]
    assert all(host in header for host in assistant.FIREWALL_DEFAULT_HOSTS)
    assert (
        capsys.readouterr().out == f"::notice::{assistant.runner_trade_off(HARDENED)}\n"
    )


def test_harden_runner_gets_each_host_once_in_small_letters(
    monkeypatch, tmp_path, root
):
    (root / FIREWALL_PATH).write_text(
        "mode: enforce\nallow:\n  - API.Example.com\n  - api.example.com\n"
        "  - GitHub.com\n",
        encoding="utf-8",
    )
    code, outputs, _ = run_main(
        monkeypatch,
        tmp_path,
        root,
        None,
        "endpoints",
        ISSUE_ASSISTANT_AI_RUNNER=HARDENED,
    )
    assert code == 0
    assert endpoints_of(outputs) == [
        (host, port)
        for host in ("github.com", "codeload.github.com", "api.example.com")
        for port in (443, 80)
    ]


@pytest.mark.parametrize("runner", ["", "ubuntu-24.04-firewall"])
def test_the_firewall_runner_needs_no_endpoints(
    monkeypatch, tmp_path, root, runner, capsys
):
    """Its firewall reads the policy itself, so Harden-Runner doesn't run there."""
    (root / FIREWALL_PATH).unlink()
    code, outputs, _ = run_main(
        monkeypatch,
        tmp_path,
        root,
        None,
        "endpoints",
        ISSUE_ASSISTANT_AI_RUNNER=runner,
    )
    assert (code, outputs) == (0, "endpoints=\n")
    assert capsys.readouterr().out == (
        "The AI runs on ubuntu-24.04-firewall, whose firewall blocks every host but "
        "those of .github/egress-firewall.yaml.\n"
    )


@pytest.mark.parametrize(
    "runner",
    [
        "self-hosted",
        "ubuntu-latest",
        "ubuntu-24.04-arm",
        "ubuntu-24.04-firewall-2",
        "windows-2025",
        "ubuntu-24.04 ",
        "ubuntu-24.4",
    ],
)
def test_a_runner_where_nothing_would_block_the_hosts_is_refused(
    monkeypatch, tmp_path, root, runner, capsys
):
    """Harden-Runner blocks on GitHub's runners of Ubuntu; the AI runs on no other
    runner, and on none whose image may change without a test, such as
    ubuntu-latest. check names the runner too, when the CI passes the variable."""
    (problem,) = assistant.runner_problems(runner)
    assert problem.startswith(
        f"ISSUE_ASSISTANT_AI_RUNNER names the runner {runner} for the AI, which runs "
        "only on ubuntu-24.04-firewall or on one of GitHub's runners of Ubuntu with a "
        "fixed version, such as ubuntu-24.04, "
    )
    assert problem.endswith("Unset the variable, or name such a runner.")
    code, outputs, _ = run_main(
        monkeypatch,
        tmp_path,
        root,
        None,
        "endpoints",
        ISSUE_ASSISTANT_AI_RUNNER=runner,
    )
    assert (code, outputs) == (1, "")
    assert capsys.readouterr().out == f"::error::{problem}\n"
    code, _, _ = run_main(
        monkeypatch, tmp_path, root, None, "check", ISSUE_ASSISTANT_AI_RUNNER=runner
    )
    assert code == 1
    assert f"::error::{problem}\n" in capsys.readouterr().out


@pytest.mark.parametrize("runner", [HARDENED, "ubuntu-26.04"])
def test_check_says_what_the_ai_gives_up_on_another_runner(
    monkeypatch, tmp_path, root, runner, capsys
):
    code, _, summary = run_main(
        monkeypatch, tmp_path, root, None, "check", ISSUE_ASSISTANT_AI_RUNNER=runner
    )
    assert code == 0 and "Everything fits." in summary
    trade_off = assistant.runner_trade_off(runner)
    assert trade_off.startswith(
        f"The AI runs on {runner} in place of ubuntu-24.04-firewall: Harden-Runner "
        "blocks every host but those of .github/egress-firewall.yaml for the job "
        "analyze, where GitHub's firewall blocks them for the whole machine"
    )
    assert "a subscription of StepSecurity" in trade_off
    assert capsys.readouterr().out.startswith(f"::notice::{trade_off}\n")


@pytest.mark.parametrize("runner", ["", "ubuntu-24.04-firewall"])
def test_check_says_nothing_of_the_firewall_runner(
    monkeypatch, tmp_path, root, runner, capsys
):
    code, _, _ = run_main(
        monkeypatch, tmp_path, root, None, "check", ISSUE_ASSISTANT_AI_RUNNER=runner
    )
    assert code == 0 and "::notice::" not in capsys.readouterr().out


@pytest.mark.parametrize(
    ("policy", "problems"),
    [
        (None, [f".github/egress-firewall.yaml is missing; {REINSTALL}"]),
        (
            "mode: enforce\n",
            [f".github/egress-firewall.yaml allows no host; {REINSTALL}"],
        ),
        (
            "- api.github.com\n",
            [f".github/egress-firewall.yaml allows no host; {REINSTALL}"],
        ),
        (
            "allow: api.github.com\n",
            [f".github/egress-firewall.yaml allows no host; {REINSTALL}"],
        ),
        (
            "allow:\n  - api.github.com\n  - '*.example.com'\n  - example.com:8443\n"
            "  - example.com/path\n  - localhost\n  - [nested]\n",
            [
                f".github/egress-firewall.yaml: name the host {host} in full"
                for host in (
                    "*.example.com",
                    "example.com:8443",
                    "example.com/path",
                    "localhost",
                    "['nested']",
                )
            ],
        ),
    ],
)
def test_harden_runner_gets_only_hosts_named_in_full(
    monkeypatch, tmp_path, root, policy, problems, capsys
):
    """Harden-Runner reads its endpoints as words: a policy that it can't take as
    it is stops the job event, before the AI runs on a runner without its block."""
    path = root / FIREWALL_PATH
    if policy is None:
        path.unlink()
    else:
        path.write_text(policy, encoding="utf-8")
    code, outputs, _ = run_main(
        monkeypatch,
        tmp_path,
        root,
        None,
        "endpoints",
        ISSUE_ASSISTANT_AI_RUNNER=HARDENED,
    )
    assert (code, outputs) == (1, "")
    assert capsys.readouterr().out == "".join(
        f"::error::{problem}\n" for problem in problems
    )


def test_gitignore_is_checked_and_actionlint_needs_no_runner(config, root):
    # The runner of the AI is in the reusable workflow, which actionlint doesn't
    # read in a repository that calls it.
    (root / ".github/actionlint.yaml").write_text(
        "self-hosted-runner:\n  labels: []\n", encoding="utf-8"
    )
    (root / ".gitignore").write_text("/build/\n", encoding="utf-8")
    assert assistant.check_workflows(config) == [".gitignore: add /.issue-assistant/"]
    (root / ".gitignore").unlink()
    assert assistant.check_workflows(config) == [".gitignore: add /.issue-assistant/"]


def test_the_related_repositories_are_checked(config):
    names = (
        "owner/server",
        "Owner/Server",
        "owner/project",
        "no repository",
        *(f"owner/app{number}" for number in range(5)),
    )
    checked = dataclasses.replace(config, related_repositories=names)
    assert assistant.check_config(checked) == [
        f"{CONFIG}: [related] names Owner/Server twice",
        f"{CONFIG}: [related] owner/project is this repository",
        f"{CONFIG}: [related] no repository is no repository; name it as owner/name",
        f"{CONFIG}: [related] names more than 5 repositories; an analysis searches "
        "the first 5",
    ]
    fine = dataclasses.replace(config, related_repositories=names[:1])
    assert assistant.check_config(fine) == []


def test_config_problems_are_found(config, root):
    broken = dataclasses.replace(
        config,
        engine="unknown",
        hosts=frozenset({"https://evil.example/path"}),
        max_per_day=-1,
        max_per_author_per_day=True,
        release_replies="2",
        max_issues=-5,
    )
    assert assistant.check_config(broken) == [
        ".github/issue-assistant/config.toml: unknown engine unknown; known engines: claude, codex, copilot, gemini",
        ".github/issue-assistant/config.toml: https://evil.example/path is no host name",
        ".github/issue-assistant/config.toml: max_per_day must be a whole number, "
        "0 or more",
        ".github/issue-assistant/config.toml: max_per_author_per_day must be a whole "
        "number, 0 or more",
        ".github/issue-assistant/config.toml: release_replies must be a whole number, "
        "0 or more",
        ".github/issue-assistant/config.toml: max_issues must be a whole number, "
        "0 or more",
    ]
    (root / "SUPPORT.md").write_text("An AI reads your issue.", encoding="utf-8")
    missing = dataclasses.replace(config, notices=("SUPPORT.md", "docs/missing.md"))
    assert assistant.check_config(missing) == [
        "SUPPORT.md: say that Claude by Anthropic reads the issues",
        "docs/missing.md is missing; it tells reporters who reads their issue",
    ]


LIFECYCLE_WHERE = ".github/issue-assistant/config.toml: [lifecycle]"


def test_the_periods_of_the_lifecycle_are_checked(config):
    assert assistant.check_lifecycle(config) == []
    broken = assistant.Lifecycle(
        remind_after_days=0,
        close_after_days=0,
        duplicate_grace_days=4000,
        reopen_after_release_days=60,
        max_follow_ups=-1,
        exempt_upvotes=-2,
        lock_closed_after_days=30,
    )
    assert assistant.check_lifecycle(dataclasses.replace(config, lifecycle=broken)) == [
        f"{LIFECYCLE_WHERE} remind_after_days must be 1 to 3650",
        f"{LIFECYCLE_WHERE} close_after_days must be 2 to 3650",
        f"{LIFECYCLE_WHERE} duplicate_grace_days must be 1 to 3650",
        f"{LIFECYCLE_WHERE} max_follow_ups must be 0 to 3650",
        f"{LIFECYCLE_WHERE} exempt_upvotes must be 0 to 3650",
        f"{LIFECYCLE_WHERE} close_after_days must be more than remind_after_days, so "
        "that the reminder comes before the closing",
        f"{LIFECYCLE_WHERE} lock_closed_after_days must be 0 or at least "
        "reopen_after_release_days, so that a reporter can still say that a release "
        "didn't fix their issue",
        # The sample's labels name the default days.
        ".github/labels.toml: needs-info says 15 and 30 days, "
        ".github/issue-assistant/config.toml says 0 and 0",
        ".github/labels.toml: stale says 15 days, "
        ".github/issue-assistant/config.toml says 0",
        ".github/labels.toml: possible-duplicate says 3 days, "
        ".github/issue-assistant/config.toml says 4000",
    ]
    fine = assistant.Lifecycle(lock_closed_after_days=30)
    assert assistant.check_lifecycle(dataclasses.replace(config, lifecycle=fine)) == []


def test_the_lifecycle_labels_name_the_configured_days_or_none(config):
    lifecycle = assistant.Lifecycle(remind_after_days=20, close_after_days=40)
    labels = tuple(
        dataclasses.replace(label, description="Waits for the reporter")
        if label.name == "needs-info"
        else label
        for label in config.labels
    )
    changed = dataclasses.replace(config, lifecycle=lifecycle, labels=labels)
    # needs-info names no days; stale names the 15 of the defaults.
    assert assistant.check(changed) == [
        ".github/labels.toml: stale says 15 days, "
        ".github/issue-assistant/config.toml says 20"
    ]


@pytest.mark.parametrize("command_", ["sweep", "event"])
def test_a_lifecycle_that_the_sweep_cannot_use_stops_every_command(
    monkeypatch, tmp_path, root, config, capsys, command_
):
    settings = root / assistant.SETTINGS / "config.toml"
    settings.write_text(
        settings.read_text("utf-8") + "\n[lifecycle]\nremind_after_days = 0\n",
        encoding="utf-8",
    )
    code, _, _ = run_main(monkeypatch, tmp_path, root, FakeGitHub(config), command_)
    assert code == 1
    assert (
        f"::error::{LIFECYCLE_WHERE} remind_after_days must be 1 to 3650"
        in capsys.readouterr().out
    )


def test_the_lifecycle_is_read_from_config_toml(root):
    settings = root / assistant.SETTINGS / "config.toml"
    settings.write_text(
        settings.read_text("utf-8")
        + '\n[lifecycle]\nclose_after_days = 60\nexempt_labels = ["keep"]\n',
        encoding="utf-8",
    )
    lifecycle = assistant.load_config(root, REPOSITORY).lifecycle
    assert lifecycle == assistant.Lifecycle(
        close_after_days=60, exempt_labels=("keep",)
    )
    assert lifecycle.warning_days == 45


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
    # The folder of an unpacked package of a release: no git checkout, so the
    # commit and the tag are given.
    package = tmp_path / "package"
    package.mkdir()
    monkeypatch.setattr(assistant, "TOOL", package)
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
    out = capsys.readouterr().out
    assert "wrote .github/workflows/issue-assistant.yml" in out
    # What is left to do, with the command of the check of this checkout.
    assert out.splitlines()[-1] == (
        "next: describe the project in .github/issue-assistant/project.md, run "
        f"python3 {package.as_posix()}/issue_assistant.py check, commit, push, and "
        "try it under Actions > Issue assistant > Run workflow"
    )
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
    assert capsys.readouterr().out == (
        "::error::--ref takes the full commit hash of the release, not 'main'; leave "
        "it out to take the commit of the checkout.\n"
    )
    code, _, _ = run_main(monkeypatch, tmp_path, target, None, "install")
    assert code == 1
    assert capsys.readouterr().out == (
        f"::error::{package.as_posix()} is no git checkout, so give --ref and "
        "--version, or run install from a checkout of a release: git clone --depth 1 "
        "--branch vX.Y.Z https://github.com/Dennis-Otto/issue-assistant\n"
    )


def test_install_takes_the_release_of_its_checkout(monkeypatch, tmp_path, capsys):
    tool = tmp_path / "issue-assistant"
    commit, _ = release_checkout(tool)
    monkeypatch.setattr(assistant, "TOOL", tool)
    target = tmp_path / "target"
    target.mkdir()
    code, _, _ = run_main(monkeypatch, tmp_path, target, None, "install")
    assert code == 0, capsys.readouterr().out
    workflow = (target / ".github/workflows/labels.yml").read_text("utf-8")
    assert f"/reusable-labels.yml@{commit} # v9.8.7" in workflow


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
    # The rules of every task, and a prompt for each, which write_context reads.
    assert sorted(path.name for path in assistant.PROMPTS.iterdir()) == sorted(
        f"{name}.md" for name in ("rules", "answer", *assistant.MODES)
    )
    assert tool / "templates" == assistant.TEMPLATES
    for starter in assistant.STARTERS:
        assert (assistant.TEMPLATES / starter).is_file()
    for name in assistant.TEMPLATE_WORKFLOWS:
        assert (assistant.TEMPLATES / assistant.WORKFLOWS / name).is_file()
    assert (assistant.TEMPLATES / assistant.FIREWALL).is_file()
    # The catalog of every language of the comments, English first of all.
    assert (tool / "locales" / "en.toml").is_file()
    for name in (
        *assistant.FORM_STARTERS.values(),
        assistant.CHOOSER,
        assistant.NOTICE,
        assistant.NOTICE_DE,
    ):
        assert (assistant.TEMPLATES / name).is_file()
    # The version of the release, for the command that check names.
    assert tool / "version.txt" == assistant.VERSION_FILE
    assert assistant.VERSION_FILE.is_file()


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


def test_yaml_that_yq_cant_read_is_named(monkeypatch, tmp_path):
    def run(command, **kwargs):
        raise assistant.subprocess.CalledProcessError(
            1, command, "", "Error: bad file: yaml: line 2\n"
        )

    monkeypatch.setitem(assistant.sys.modules, "yaml", None)
    monkeypatch.setattr(assistant.subprocess, "run", run)
    with pytest.raises(assistant.SettingsError) as raised:
        assistant.load_yaml(tmp_path / "form.yml", "form.yml")
    assert raised.value.problems == [
        "form.yml is no valid YAML: Error: bad file: yaml: line 2"
    ]


def test_yaml_without_pyyaml_or_yq_says_what_to_install(monkeypatch, tmp_path):
    def run(command, **kwargs):
        raise FileNotFoundError(command[0])

    monkeypatch.setitem(assistant.sys.modules, "yaml", None)
    monkeypatch.setattr(assistant.subprocess, "run", run)
    with pytest.raises(assistant.AssistantError, match="needs PyYAML or yq"):
        assistant.load_yaml(tmp_path / "workflow.yml")
