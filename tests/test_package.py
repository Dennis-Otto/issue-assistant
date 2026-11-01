"""The shape of the package: its layers, its length, and how the action starts it.

Decision 0003 keeps the action on the standard library, and adds that its code is a
package of modules, each no longer than a reviewer can follow. These tests read the
code as text, so the Mutation workflow leaves them out.
"""

import ast
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml

import issue_assistant as assistant

pytestmark = pytest.mark.mutation_unsafe

ROOT = Path(assistant.__file__).parents[1]
PACKAGE = ROOT / "issue_assistant"
# The modules besides those that hold code: the one that hands on the names of the
# others, and the one that runs the command line.
FRAME = {"__init__", "__main__"}
# What `check` reads YAML with where a machine has it, and yq where it hasn't.
OPTIONAL = {"yaml"}
LONGEST_MODULE = 1500
LONGEST_FUNCTION = 150


def tree_of(name: str) -> ast.Module:
    return ast.parse((PACKAGE / f"{name}.py").read_text(encoding="utf-8"))


def layers() -> list[str]:
    """The modules in the order of __init__.py, from the ground up."""
    return [
        statement.module
        for statement in tree_of("__init__").body
        if isinstance(statement, ast.ImportFrom)
        and statement.level == 1
        and statement.module is not None
    ]


def modules() -> list[str]:
    return sorted(path.stem for path in PACKAGE.glob("*.py") if path.stem not in FRAME)


def test_the_package_hands_on_every_module_once():
    assert sorted(layers()) == modules()


def test_a_module_imports_only_from_those_before_it():
    order = layers()
    for position, name in enumerate(order):
        for node in ast.walk(tree_of(name)):
            if isinstance(node, ast.ImportFrom) and node.level:
                assert node.level == 1 and node.module in order[:position], (
                    f"{name} imports {node.module}, which isn't before it in "
                    "issue_assistant/__init__.py. Move what both need to a module "
                    "further down, so that no two modules need each other."
                )


@pytest.mark.parametrize("name", sorted(path.stem for path in PACKAGE.glob("*.py")))
def test_the_package_runs_on_the_standard_library_alone(name):
    for node in ast.walk(tree_of(name)):
        found = []
        if isinstance(node, ast.Import):
            found = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and not node.level:
            found = [node.module or ""]
        for module in found:
            assert module.partition(".")[0] in sys.stdlib_module_names | OPTIONAL, (
                f"{name} imports {module}; decision 0003 keeps the action on the "
                "standard library."
            )


@pytest.mark.parametrize("name", modules())
def test_no_module_grows_past_what_a_review_can_follow(name):
    lines = (PACKAGE / f"{name}.py").read_text(encoding="utf-8").count("\n")
    assert lines <= LONGEST_MODULE, f"{name}.py has {lines} lines; split it."


@pytest.mark.parametrize("name", modules())
def test_no_function_grows_past_what_a_review_can_follow(name):
    for node in ast.walk(tree_of(name)):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            lines = node.end_lineno - node.lineno + 1
            assert lines <= LONGEST_FUNCTION, f"{name}.{node.name} has {lines} lines."


def test_no_name_of_the_package_hides_a_module():
    # The tests replace a name in its module, as assistant.cli.GitHub: a function of
    # the name of a module would take its place in the package.
    for name in modules():
        assert isinstance(getattr(assistant, name), ModuleType), name


def test_the_map_of_the_architecture_names_every_module():
    page = (ROOT / "docs/architecture/modules.md").read_text(encoding="utf-8")
    for name in modules():
        assert f"`issue_assistant/{name}.py`" in page, name


def action_command() -> list[str]:
    """The command of the action without its variable, for the --help of a test."""
    action = yaml.safe_load((ROOT / "action.yml").read_text(encoding="utf-8"))
    (step,) = (step for step in action["runs"]["steps"] if step.get("id") == "run")
    variable, python, *arguments, command = shlex.split(step["run"])
    assert variable == "PYTHONPATH=$GITHUB_ACTION_PATH"
    assert (python, command) == ("python3", "$COMMAND")
    return [sys.executable, *arguments, "--help"]


@pytest.fixture
def tool(tmp_path: Path) -> Path:
    """The files that the action needs to start, in a folder of their own."""
    folder = tmp_path / "tool"
    shutil.copytree(PACKAGE, folder / "issue_assistant")
    shutil.copytree(ROOT / "locales", folder / "locales")
    for leftover in folder.rglob("__pycache__"):
        shutil.rmtree(leftover)
    return folder


@pytest.fixture
def hostile_checkout(tmp_path: Path) -> Path:
    """A checkout of a pull request with modules of the names that the action imports."""
    folder = tmp_path / "checkout"
    (folder / "issue_assistant").mkdir(parents=True)
    run = 'raise SystemExit("ran the code of the checkout")\n'
    (folder / "issue_assistant" / "__init__.py").write_text(run, encoding="utf-8")
    (folder / "json.py").write_text(run, encoding="utf-8")
    return folder


def started(command: list[str], tool: Path, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        cwd=cwd,
        env={**os.environ, "PYTHONPATH": str(tool), "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        encoding="utf-8",
        check=False,
    )


def test_the_action_runs_no_code_of_the_checkout(tool, hostile_checkout):
    result = started(action_command(), tool, hostile_checkout)
    assert result.returncode == 0, result.stderr
    assert "usage: issue_assistant" in result.stdout
    # The test sees it: without -P, the folder of the command comes first.
    without = [word for word in action_command() if word != "-P"]
    result = started(without, tool, hostile_checkout)
    assert "ran the code of the checkout" in result.stderr


def test_the_action_leaves_its_folder_as_its_commit_has_it(tool, hostile_checkout):
    assert started(action_command(), tool, hostile_checkout).returncode == 0
    assert not list(tool.rglob("__pycache__"))
