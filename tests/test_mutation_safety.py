"""The tests that read the package as text or time it carry the marker mutation_unsafe.

The Mutation workflow runs the tests in mutmut's copy of the repository, mutants/,
with `-m "not mutation_unsafe"`. There each module of issue_assistant/ holds every
mutant of a function beside it, as a function of its own with each changed string
and number. A test that reads a module as text sees them all, whichever mutant runs:
it can't notice a mutant, and it fails without any, which stops the whole weekly run
before it measures anything. Such a test gets the marker, and the checks of behaviour
that it made move into a test of their own, which the mutants run against.

A test that times a reading of the package can't pass there either (#553): every
call of a function of the package goes through mutmut's trampoline, which records
it, so that a reading takes several times as long in the copy, and in mutmut's own
process, where it collects its stats, dozens of times as long as without it. The
time measured there is mutmut's, and a bound of time such as CEILING of
tests/support.py fails on it without any mutant. Such a test gets the marker too; the
check of every pull request still runs it, and what it checks besides the clock gets
a test of its own.

This guard finds each such test with `ast`, following the helpers, fixtures and
constants of the test's own module (not those of conftest.py or support.py, whose
helpers that time a reading it knows by name), so that one added later fails here,
in every pull request, and not only in the next weekly run. It finds the timing only
through those helpers: a test that reads a clock such as time.perf_counter() around a
reading itself isn't found, so time a reading with the helpers of tests/support.py.
"""

import ast
from collections.abc import Callable, Iterable, Iterator
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
# The helpers of tests/support.py and of the timing tests that time a reading of the
# package, whatever a test gives them: the growth of a reading and its CEILING, or the
# processor time of three tries.
TIMERS = {"holds_its_growth", "holds_the_growth_of", "holds_growth", "fast"}
# The helpers of tests/support.py that time what they are given. A test that gives
# them sides of its own, which call nothing of the package, times only those, as the
# tests of growth() itself do, and passes in mutmut's copy.
GROWTHS = {"growth", "plain_growth", "too_slow"}

Function = ast.FunctionDef | ast.AsyncFunctionDef


@dataclass(frozen=True)
class Unmarked:
    """A test that reads the package as text, or times it, without the marker."""

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
    elif isinstance(node, ast.For | ast.comprehension) and isinstance(
        node.target, ast.Name
    ):
        # The name of a loop takes each value of what it runs over, as folder does
        # in `for folder in ("docs", "issue_assistant")`.
        yield node.target.id, node.iter


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


def names_in(node: ast.AST) -> set[str]:
    return {child.id for child in ast.walk(node) if isinstance(child, ast.Name)}


def spread(functions: list[tuple[Function, bool]], found: set[str]) -> set[str]:
    """`found` with each function that calls one of them, or each test that takes
    one as its fixture, and so on, until no function is added."""
    uses = [
        (
            function.name,
            names_in(function) | {argument.arg for argument in function.args.args},
        )
        for function, _ in functions
    ]
    found = set(found)
    while added := {name for name, used in uses if name not in found and found & used}:
        found |= added
    return found


def unmarked(
    functions: list[tuple[Function, bool]], found: set[str], path: str
) -> list[Unmarked]:
    """The tests among `found` that neither they nor their class mark."""
    return [
        Unmarked(path, function.lineno, function.name)
        for function, class_marked in functions
        if function.name.startswith("test")
        and function.name in found
        and not class_marked
        and not marked(function.decorator_list)
    ]


def unmarked_readers(source: str, path: str) -> list[Unmarked]:
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
    return unmarked(functions, spread(functions, readers), path)


def bound(nodes: Iterable[ast.AST]) -> Iterator[tuple[str, ast.AST]]:
    """The names that statements bind, each with what it binds: a value, or a
    function or a class with its body. Each name of a target that unpacks takes the
    whole value, as eighth and whole do in `eighth, whole = reading(...), ...`."""
    for node in nodes:
        if isinstance(node, Function | ast.ClassDef):
            yield node.name, node
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                for name in names_in(target):
                    yield name, node.value
        else:
            yield from assigned(node)


def reaching(names: set[str], bindings: list[tuple[str, ast.AST]]) -> set[str]:
    """`names` with each name of `bindings` whose value or body uses one of them, and
    so on, until no name is added."""
    uses = [(name, names_in(value)) for name, value in bindings]
    names = set(names)
    while added := {name for name, used in uses if name not in names and names & used}:
        names |= added
    return names


def package_names(tree: ast.Module) -> set[str]:
    """The names of a module that reach the package: those under which it imports
    the package or a name of it, and its constants, functions and classes that use
    one of them, as READINGS and reading() of tests/test_assistant.py do."""
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {
                alias.asname or alias.name.partition(".")[0]
                for alias in node.names
                if alias.name.partition(".")[0] == PACKAGE
            }
        elif isinstance(node, ast.ImportFrom) and (
            not node.level and (node.module or "").partition(".")[0] == PACKAGE
        ):
            imported |= {alias.asname or alias.name for alias in node.names}
    return reaching(imported, list(bound(tree.body)))


def called(node: ast.AST) -> str | None:
    """The name of the function that a call calls, by its name or as an attribute."""
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            return node.func.attr
    return None


def times_the_package(function: Function, package: set[str]) -> bool:
    """Whether a function times a reading of the package: it calls a helper of
    TIMERS, or one of GROWTHS with a side that reaches the package through a name of
    its module or one that it binds itself, as a side defined within the test."""
    reach = reaching(package, list(bound(ast.walk(function))))
    for node in ast.walk(function):
        name = called(node)
        if name in TIMERS:
            return True
        if name in GROWTHS and isinstance(node, ast.Call):
            given = [*node.args, *(keyword.value for keyword in node.keywords)]
            if any(reach & names_in(argument) for argument in given):
                return True
    return False


def replaces_the_growth(function: Function) -> bool:
    """Whether a test puts a stand-in in the place of a helper of GROWTHS, as
    `monkeypatch.setitem(globals(), "growth", ...)` does: it times nothing, and its
    helper of the timing only hands on the figures of the stand-in."""
    return any(
        isinstance(node, ast.Call)
        and called(node) in {"setattr", "setitem"}
        and any(
            isinstance(argument, ast.Constant) and argument.value in GROWTHS
            for argument in node.args
        )
        for node in ast.walk(function)
    )


def unmarked_timers(source: str, path: str) -> list[Unmarked]:
    """The tests of a module that time a reading of the package but lack the marker.

    A test times it when it does itself, or calls a helper or takes a fixture of its
    module that does; not when it replaces the helper that times.
    """
    # A module that names no helper of the timing times nothing, and isn't parsed.
    if not any(name in source for name in TIMERS | GROWTHS):
        return []
    tree = ast.parse(source)
    if marks_the_module(tree):
        return []
    functions = list(functions_of(tree))
    # Only a function that calls a helper of the timing may time a reading itself,
    # and only one that calls a helper of GROWTHS needs the names of the module.
    calls = [
        (function, set(map(called, ast.walk(function)))) for function, _ in functions
    ]
    package = (
        package_names(tree) if any(names & GROWTHS for _, names in calls) else set()
    )
    timers = {
        function.name
        for function, names in calls
        if names & (TIMERS | GROWTHS) and times_the_package(function, package)
    }
    # A test with a stand-in for growth() times nothing through the helpers of the
    # growth, but fast() doesn't go through growth(): one that calls it still times.
    timers = spread(functions, timers) - {
        function.name
        for function, names in calls
        if names & {"setattr", "setitem"}
        and "fast" not in names
        and replaces_the_growth(function)
    }
    return unmarked(functions, timers, path)


def unmarked_in(
    folder: Path,
    find: Callable[[str, str], list[Unmarked]] = unmarked_readers,
) -> list[Unmarked]:
    return [
        test
        for path in sorted(folder.glob("test*.py"))
        for test in find(path.read_text(encoding="utf-8"), f"{folder.name}/{path.name}")
    ]


def explanation(readers: list[Unmarked]) -> str:
    return (
        f"These tests read the package {PACKAGE} as text: "
        f"{', '.join(map(str, readers))}. In "
        "mutmut's copy each module holds every mutant beside its function, so such a "
        "test fails there without any mutant and stops the weekly Mutation run. Mark "
        f"each with @pytest.mark.{MARKER}, which the run leaves out, and move the "
        "checks of behaviour that it makes into a test of its own (CONTRIBUTING.md, "
        "Tests)."
    )


def timing_explanation(timers: list[Unmarked]) -> str:
    return (
        f"These tests time a reading of the package {PACKAGE}: "
        f"{', '.join(map(str, timers))}. In mutmut's copy every call of the package "
        "goes through a trampoline that records it, so the time measured there is "
        "mutmut's: such a test fails on its bound of time without any mutant and "
        f"stops the weekly Mutation run. Mark each with @pytest.mark.{MARKER}, which "
        "the run leaves out while the check of every pull request still runs it, and "
        "give what it checks besides the clock a test of its own, without a clock "
        "(tests/README.md)."
    )


def test_every_test_that_reads_the_package_as_text_is_marked():
    readers = unmarked_in(TESTS)
    assert not readers, explanation(readers)


def test_every_test_that_times_a_reading_of_the_package_is_marked():
    timers = unmarked_in(TESTS, unmarked_timers)
    assert not timers, timing_explanation(timers)


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
    "a folder among others, in a loop": (
        'def test_a():\n    for folder in ("docs", "issue_assistant"):\n'
        "        (ROOT / folder).rglob('*')\n"
    ),
    "a folder among others, in a comprehension": (
        "def test_a():\n    texts = [path for folder in ('docs', 'issue_assistant')"
        " for path in (ROOT / folder).rglob('*')]\n"
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
    "other folders, in a loop": (
        'def test_a():\n    for folder in ("docs", "tests"):\n'
        "        (ROOT / folder).rglob('*')\n"
    ),
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
        Unmarked("tests/test_new.py", line, "test_a")
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
    assert readers == [Unmarked("tests/test_new.py", 2, "test_a")]
    assert "tests/test_new.py:2 test_a" in explanation(readers)
    assert "@pytest.mark.mutation_unsafe" in explanation(readers)


# Tests that time a reading of the package, each as the module test_new.py.
TIMING = {
    "the growth of a reading": (
        "def test_a(record_property):\n"
        "    holds_its_growth(PARTS, 'render_issue', 'a text', record_property)\n"
    ),
    "the growth of a function": (
        "def test_a(record_property):\n"
        "    holds_the_growth_of(assistant.shown_text, PARTS, 'a', record_property)\n"
    ),
    "the growth of two sides": (
        "def test_a(record_property):\n"
        "    holds_growth('x', 'a', lambda: 1, lambda: 2, record_property)\n"
    ),
    "three tries": "def test_a():\n    assert fast(2, assistant.sanitize, 'x')\n",
    "growth() of a reading": (
        "def test_a():\n"
        "    growth(lambda: assistant.excerpt('a', 9), lambda: assistant.excerpt('aa', 9))\n"
    ),
    "growth() of the module support": (
        "def test_a():\n"
        "    support.growth(lambda: assistant.excerpt('a', 9), lambda: None)\n"
    ),
    "plain_growth() of a side defined within the test": (
        "def test_a():\n"
        "    def convert(html):\n"
        "        return lambda: assistant.markdown_of(html, html)\n"
        "    plain_growth(convert('a'), convert('aa'))\n"
    ),
    "too_slow() of a side of a helper of the module": (
        "READ = {'excerpt': lambda text: assistant.excerpt(text, 9)}\n"
        "def reading(text):\n    return lambda: READ['excerpt'](text)\n"
        "def test_a():\n"
        "    eighth, whole = reading('a'), reading('aa')\n"
        "    too_slow(0.6, 8, eighth, whole, 0.5, 3, measure=growth)\n"
    ),
    "a name imported from the package": (
        "from issue_assistant.converter import tree_of\n"
        "def test_a():\n    growth(lambda: tree_of('a'), lambda: tree_of('aa'))\n"
    ),
    "a helper of a helper": (
        "def timed(text):\n    return fast(1, assistant.excerpt, text, 9)\n"
        "def test_a():\n    assert timed('a')\n"
    ),
    "a stand-in for growth() but fast() of a reading": (
        "def test_a(monkeypatch, record_property):\n"
        "    monkeypatch.setitem(globals(), 'growth', lambda *_: None)\n"
        "    holds_its_growth(PARTS, 'render_issue', 'a text', record_property)\n"
        "    assert fast(2, assistant.sanitize, 'x')\n"
    ),
    "a method of a class that isn't marked": (
        "class TestA:\n"
        "    def test_a(self):\n        assert fast(2, assistant.sanitize, 'x')\n"
    ),
}

# Tests that time nothing of the package, or are marked.
UNTIMED = {
    "marked": (
        "@pytest.mark.mutation_unsafe\n"
        "def test_a():\n    assert fast(2, assistant.sanitize, 'x')\n"
    ),
    "a marked module": (
        "pytestmark = pytest.mark.mutation_unsafe\n"
        "def test_a():\n    assert fast(2, assistant.sanitize, 'x')\n"
    ),
    "growth() of sides of its own": (
        "def test_a():\n"
        "    def linear(text):\n        return lambda: text.count('x')\n"
        "    assert growth(linear('x'), linear('xx')).times < GROWTH\n"
    ),
    "too_slow() of built-in functions": (
        "def test_a():\n    assert too_slow(0.9, 8, print, print, 0.5, 1)\n"
    ),
    "plain_growth() of a helper that calls nothing of the package": (
        "def sides():\n    return lambda: None, lambda: None\n"
        "def test_a():\n    eighth, whole = sides()\n    plain_growth(eighth, whole)\n"
    ),
    "a stand-in for growth()": (
        "def test_a(monkeypatch, record_property):\n"
        "    monkeypatch.setitem(globals(), 'growth', lambda *_: None)\n"
        "    holds_its_growth(PARTS, 'render_issue', 'a text', record_property)\n"
    ),
    "a stand-in for growth() in a module": (
        "def test_a(monkeypatch, record_property):\n"
        "    monkeypatch.setattr(support, 'growth', lambda *_: None)\n"
        "    holds_the_growth_of(assistant.shown_text, PARTS, 'a', record_property)\n"
    ),
    "a reading without a clock": (
        "def test_a():\n    assert assistant.excerpt('a', 9) == 'a'\n"
    ),
    "a helper that times, which no test calls": (
        "def timed():\n    return fast(1, assistant.excerpt, 'a', 9)\n"
        "def test_a():\n    assert True\n"
    ),
}


@pytest.mark.parametrize("name", TIMING)
def test_the_guard_finds_a_test_that_times_the_package(name):
    module = IMPORT + TIMING[name]
    line = next(
        number
        for number, text in enumerate(module.splitlines(), 1)
        if text.lstrip().startswith("def test_a")
    )
    assert unmarked_timers(module, "tests/test_new.py") == [
        Unmarked("tests/test_new.py", line, "test_a")
    ]


@pytest.mark.parametrize("name", UNTIMED)
def test_the_guard_leaves_alone_a_test_that_times_nothing_of_the_package(name):
    assert unmarked_timers(IMPORT + UNTIMED[name], "tests/test_new.py") == []


def test_the_guard_fails_for_a_new_timing_test_and_names_the_fix(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_new.py").write_text(
        IMPORT + TIMING["the growth of a reading"], encoding="utf-8"
    )
    timers = unmarked_in(tests, unmarked_timers)
    assert timers == [Unmarked("tests/test_new.py", 2, "test_a")]
    assert unmarked_in(tests) == []
    message = timing_explanation(timers)
    assert "tests/test_new.py:2 test_a" in message
    assert "@pytest.mark.mutation_unsafe" in message
    assert "trampoline" in message
