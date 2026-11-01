"""The tests that read the package as text carry the marker mutation_unsafe.

The Mutation workflow runs the tests in mutmut's copy of the repository, mutants/,
with `-m "not mutation_unsafe"`. There each module of issue_assistant/ holds every
mutant of a function beside it, as a function of its own with each changed string
and number. A test that reads a module as text sees them all, whichever mutant runs:
it can't notice a mutant, and it fails without any, which stops the whole weekly run
before it measures anything. Such a test gets the marker, and the checks of behaviour
that it made move into a test of their own, which the mutants run against.

This guard finds each such test with `ast`, following the helpers, fixtures and
constants of the test's own module (not those of conftest.py or support.py), so
that one added later fails here, in every pull request, and not only in the next
weekly run.
"""

import ast
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
PACKAGE = "issue_assistant"
MARKER = "mutation_unsafe"
# The calls that read the file whose path they are called on or are given, or list
# the modules of the folder.
READS = {"read_text", "read_bytes", "open", "glob", "rglob", "iterdir"}
# The functions of inspect that read the source of what they are given, and that of
# tests/support.py that reads the whole package.
SOURCE_OF = {"getsource", "getsourcelines", "findsource", "package_source"}

Function = ast.FunctionDef | ast.AsyncFunctionDef


@dataclass(frozen=True)
class Reader:
    """A test that reads the package as text without the marker."""

    path: str
    line: int
    name: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line} {self.name}"


def assigned(node: ast.AST) -> Iterator[tuple[str, ast.expr]]:
    """The names that a statement or an expression binds, each with its value."""
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name):
                yield target.id, node.value
    elif (
        isinstance(node, ast.AnnAssign | ast.NamedExpr)
        and isinstance(node.target, ast.Name)
        and node.value is not None
    ):
        yield node.target.id, node.value


@dataclass
class Scope:
    """What names the package, and what reads it as text, in a module or a function."""

    # The names under which the module imports issue_assistant.
    modules: frozenset[str]
    # The names whose value is a path in the package, as PACKAGE = ROOT / "..." is.
    paths: set[str]
    # The names whose value is its text, as SOURCE = SCRIPT.read_text() is.
    texts: set[str]

    @classmethod
    def of(cls, tree: ast.Module) -> "Scope":
        modules = frozenset(
            alias.asname or alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
            if alias.name == PACKAGE
        )
        scope = cls(modules, set(), set())
        scope.bind(tree.body)
        return scope

    def inner(self, function: Function) -> "Scope":
        """The scope of a function, with the names that it binds itself."""
        scope = Scope(self.modules, set(self.paths), set(self.texts))
        scope.bind(ast.walk(function))
        return scope

    def bind(self, nodes: Iterable[ast.AST]) -> None:
        for node in nodes:
            for name, value in assigned(node):
                if self.reads(value):
                    self.texts.add(name)
                elif self.names_the_package(value):
                    self.paths.add(name)

    def names_the_package(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Constant):
            return node.value == PACKAGE or (
                isinstance(node.value, str)
                and node.value.startswith(f"{PACKAGE}/")
                and node.value.endswith(".py")
            )
        if isinstance(node, ast.Name):
            return node.id in self.paths
        if isinstance(node, ast.Attribute):
            # The folders around the package, whose files mutmut doesn't change: the
            # parent of a module is the package, and that of a folder or parents[1] isn't.
            if node.attr == "parents":
                return False
            if node.attr == "parent":
                return not isinstance(
                    node.value, ast.Attribute
                ) and self.names_the_package(node.value)
            if node.attr == "__file__":
                base = node.value
                while isinstance(base, ast.Attribute):
                    base = base.value
                return isinstance(base, ast.Name) and base.id in self.modules
        return any(
            self.names_the_package(child) for child in ast.iter_child_nodes(node)
        )

    def reads(self, node: ast.AST) -> bool:
        """Whether an expression or a statement reads the package as text."""
        return any(self.read(part) for part in ast.walk(node))

    def read(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return node.id in self.texts
        if not isinstance(node, ast.Call):
            return False
        function = node.func
        given = any(self.names_the_package(argument) for argument in node.args)
        if isinstance(function, ast.Name):
            return function.id in SOURCE_OF or (function.id == "open" and given)
        if isinstance(function, ast.Attribute):
            if function.attr in SOURCE_OF:
                return isinstance(function.value, ast.Name) and (
                    function.value.id == "inspect"
                )
            return function.attr in READS and (
                given or self.names_the_package(function.value)
            )
        return False


def marked(decorators: Iterable[ast.expr]) -> bool:
    """Whether one of the decorators is pytest.mark.mutation_unsafe."""
    return any(
        isinstance(mark, ast.Attribute) and mark.attr == MARKER
        for mark in (
            decorator.func if isinstance(decorator, ast.Call) else decorator
            for decorator in decorators
        )
    )


def marks_the_module(tree: ast.Module) -> bool:
    """Whether `pytestmark` gives every test of the module the marker."""
    return any(
        marked(value.elts if isinstance(value, ast.List | ast.Tuple) else [value])
        for statement in tree.body
        for name, value in assigned(statement)
        if name == "pytestmark"
    )


def functions_of(tree: ast.Module) -> Iterator[tuple[Function, bool]]:
    """Each function of the module and of its classes, and whether its class is marked."""
    for statement in tree.body:
        if isinstance(statement, Function):
            yield statement, False
        elif isinstance(statement, ast.ClassDef):
            for member in statement.body:
                if isinstance(member, Function):
                    yield member, marked(statement.decorator_list)


def unmarked_readers(source: str, path: str) -> list[Reader]:
    """The tests of a module that read the package as text but lack the marker.

    A test reads it when it reads it itself, or calls a helper or takes a fixture of
    its module that does, or uses a constant of its module that holds its text.
    """
    tree = ast.parse(source)
    if marks_the_module(tree):
        return []
    scope = Scope.of(tree)
    functions = list(functions_of(tree))
    readers = {
        function.name
        for function, _ in functions
        if scope.inner(function).reads(function)
    }
    # A function that calls a reader, or a test that takes one as its fixture, reads
    # too; and so on, until no function is added.
    while added := {
        function.name
        for function, _ in functions
        if function.name not in readers
        and readers
        & (
            {node.id for node in ast.walk(function) if isinstance(node, ast.Name)}
            | {argument.arg for argument in function.args.args}
        )
    }:
        readers |= added
    return [
        Reader(path, function.lineno, function.name)
        for function, class_marked in functions
        if function.name.startswith("test")
        and function.name in readers
        and not class_marked
        and not marked(function.decorator_list)
    ]


def unmarked_in(folder: Path) -> list[Reader]:
    return [
        reader
        for path in sorted(folder.glob("test*.py"))
        for reader in unmarked_readers(
            path.read_text(encoding="utf-8"), f"{folder.name}/{path.name}"
        )
    ]


def explanation(readers: list[Reader]) -> str:
    return (
        f"These tests read the package {PACKAGE} as text: "
        f"{', '.join(map(str, readers))}. In "
        "mutmut's copy each module holds every mutant beside its function, so such a "
        "test fails there without any mutant and stops the weekly Mutation run. Mark "
        f"each with @pytest.mark.{MARKER}, which the run leaves out, and move the "
        "checks of behaviour that it makes into a test of its own (CONTRIBUTING.md, "
        "Tests)."
    )


def test_every_test_that_reads_the_package_as_text_is_marked():
    readers = unmarked_in(TESTS)
    assert not readers, explanation(readers)


IMPORT = "import issue_assistant as assistant\n"

# Tests that read the package as text, each as the module test_new.py.
READERS = {
    "the text from its path": (
        'def test_a():\n    (ROOT / "issue_assistant" / "cli.py").read_text()\n'
    ),
    "the text from __file__": (
        "def test_a():\n    Path(assistant.__file__).read_text(encoding='utf-8')\n"
    ),
    "the bytes": "def test_a():\n    Path(assistant.__file__).read_bytes()\n",
    "open()": 'def test_a():\n    open(ROOT / "issue_assistant" / "cli.py").read()\n',
    "open() in with": (
        "def test_a():\n    with open(assistant.__file__) as file:\n        file.read()\n"
    ),
    "Path.open()": 'def test_a():\n    (TOOL / "issue_assistant/cli.py").open().read()\n',
    "a name of the test for its path": (
        'def test_a():\n    module = ROOT / "issue_assistant/cli.py"\n    module.read_text()\n'
    ),
    "a name of the module for its path": (
        'FOLDER = ROOT / "issue_assistant"\ndef test_a():\n    (FOLDER / "cli.py").read_text()\n'
    ),
    "the file of a module": "def test_a():\n    Path(assistant.cli.__file__).read_text()\n",
    "the modules of its folder": (
        'def test_a():\n    for module in (ROOT / "issue_assistant").glob("*.py"):\n'
        "        module.read_text()\n"
    ),
    "the folder of __file__": (
        "def test_a():\n    list(Path(assistant.__file__).parent.iterdir())\n"
    ),
    "the helper of tests/support.py": (
        "def test_a():\n    assert 'x' in package_source()\n"
    ),
    "the source of a function": (
        "def test_a():\n    inspect.getsource(assistant.main)\n"
    ),
    "getsource, imported": "def test_a():\n    getsource(assistant.main)\n",
    "a constant with its text": (
        'SOURCE = (ROOT / "issue_assistant" / "cli.py").read_text()\n'
        "def test_a():\n    assert 'x' in SOURCE\n"
    ),
    "a helper of a helper": (
        "def text():\n    return Path(assistant.__file__).read_text()\n"
        "def lines():\n    return text().splitlines()\n"
        "def test_a():\n    assert lines()\n"
    ),
    "a fixture": (
        "@pytest.fixture\ndef source():\n    return Path(assistant.__file__).read_text()\n"
        "def test_a(source):\n    assert source\n"
    ),
    "a method of a class that isn't marked": (
        "class TestA:\n    def test_a(self):\n        Path(assistant.__file__).read_text()\n"
    ),
}

# Tests that are marked or that read nothing that the mutants change.
SAFE = {
    "marked": (
        "@pytest.mark.mutation_unsafe\n"
        "def test_a():\n    Path(assistant.__file__).read_text()\n"
    ),
    "marked with a call": (
        "@pytest.mark.mutation_unsafe()\n"
        "def test_a():\n    Path(assistant.__file__).read_text()\n"
    ),
    "marked with mark imported": (
        "@mark.mutation_unsafe\ndef test_a():\n    Path(assistant.__file__).read_text()\n"
    ),
    "a marked module": (
        "pytestmark = pytest.mark.mutation_unsafe\n"
        "def test_a():\n    Path(assistant.__file__).read_text()\n"
    ),
    "a module marked in a list": (
        "pytestmark = [pytest.mark.slow, pytest.mark.mutation_unsafe]\n"
        "def test_a():\n    Path(assistant.__file__).read_text()\n"
    ),
    "a marked class": (
        "@pytest.mark.mutation_unsafe\nclass TestA:\n"
        "    def test_a(self):\n        Path(assistant.__file__).read_text()\n"
    ),
    "another file next to the package": (
        "def test_a():\n    (Path(assistant.__file__).parents[1] / 'action.yml').read_text()\n"
    ),
    "another file through the folder of the package": (
        "ROOT = Path(assistant.__file__).parent.parent\n"
        "def test_a():\n    (ROOT / 'action.yml').read_text()\n"
    ),
    "another folder": 'def test_a():\n    (ROOT / "tests").glob("*.py")\n',
    "the file of another module": (
        "def test_a():\n    Path(other.__file__).read_text()\n"
    ),
    "the name of the package in a text": (
        "def test_a():\n    assert run('python3 -m issue_assistant check')\n"
    ),
    "the package as a program": (
        "def test_a():\n    runpy.run_module('issue_assistant', run_name='__main__')\n"
    ),
    "a helper that reads, which no test calls": (
        "def text():\n    return Path(assistant.__file__).read_text()\n"
        "def test_a():\n    assert True\n"
    ),
    "a function that isn't a test": (
        "def helper():\n    return Path(assistant.__file__).read_text()\n"
    ),
}


@pytest.mark.parametrize("name", READERS)
def test_the_guard_finds_a_test_that_reads_the_package(name):
    module = IMPORT + READERS[name]
    line = next(
        number
        for number, text in enumerate(module.splitlines(), 1)
        if text.lstrip().startswith("def test_a")
    )
    assert unmarked_readers(module, "tests/test_new.py") == [
        Reader("tests/test_new.py", line, "test_a")
    ]


@pytest.mark.parametrize("name", SAFE)
def test_the_guard_leaves_alone_what_the_mutants_can_t_break(name):
    assert unmarked_readers(IMPORT + SAFE[name], "tests/test_new.py") == []


def test_the_guard_fails_for_a_new_test_and_names_the_fix(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_new.py").write_text(
        IMPORT + READERS["the text from __file__"], encoding="utf-8"
    )
    (tests / "support.py").write_text(
        IMPORT + READERS["the text from __file__"], encoding="utf-8"
    )
    readers = unmarked_in(tests)
    assert readers == [Reader("tests/test_new.py", 2, "test_a")]
    assert "tests/test_new.py:2 test_a" in explanation(readers)
    assert "@pytest.mark.mutation_unsafe" in explanation(readers)
