"""A sample repository with the issue assistant installed, as a git checkout."""

import dataclasses
import os
import shutil
import stat
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from hypothesis import settings

import issue_assistant as assistant
from support import REF, REPOSITORY

SAMPLE = Path(__file__).parent / "sample"

# Property tests try fixed inputs in the CI, so that a run repeats exactly, and new
# random ones locally and in the nightly Property tests workflow, which can replay a
# seed.
settings.register_profile("ci", deadline=None, derandomize=True, print_blob=True)
settings.register_profile("local", deadline=None)
settings.load_profile("ci" if os.environ.get("CI") else "local")


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def set_writable(folder: Path, writable: bool) -> None:
    """Allow or forbid writing to `folder` and everything in it."""
    for path in (folder, *folder.rglob("*")):
        mode = stat.S_IMODE(path.stat().st_mode)
        path.chmod(mode | stat.S_IWUSR if writable else mode & ~0o222)


def contents(folder: Path) -> dict[str, bytes | None]:
    return {
        path.relative_to(folder).as_posix(): path.read_bytes()
        if path.is_file()
        else None
        for path in folder.rglob("*")
    }


@pytest.fixture(scope="session")
def sample_root(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """The sample repository after `install`, with every file in git's index.

    The tests share it, so it is built once. It is read-only, so that a test that
    writes into it by mistake fails instead of changing what later tests read; a
    test that changes files takes `root`, a copy of its own. Windows lets a test
    create a file in a read-only folder, so the end of the session checks that
    nothing was added either.
    """
    folder = tmp_path_factory.mktemp("sample") / "project"
    shutil.copytree(SAMPLE, folder)
    assistant.install(folder, REF, "v1.0.0")
    # tracked_files reads git's index: the files need no commit, and the folder
    # needs none of the hooks and examples of git's template.
    git(folder, "init", "-q", "--template=", "-b", "main")
    git(folder, "add", "-A")
    built = contents(folder)
    set_writable(folder, False)
    yield folder
    set_writable(folder, True)
    assert contents(folder) == built, "A test changed the shared sample; give it root."


@pytest.fixture(scope="session")
def sample_config(sample_root: Path) -> assistant.Config:
    return assistant.load_config(sample_root, REPOSITORY)


@pytest.fixture
def root(sample_root: Path, tmp_path: Path) -> Path:
    """A copy of the sample repository, for a test that changes its files."""
    folder = tmp_path / "project"
    shutil.copytree(sample_root, folder)
    set_writable(folder, True)
    return folder


@pytest.fixture
def config(
    request: pytest.FixtureRequest, sample_config: assistant.Config
) -> assistant.Config:
    """The settings of the sample repository.

    In a test that takes `root`, they are those of its copy, so that `config.root`
    is the folder whose files the test changes.
    """
    if "root" in request.fixturenames:
        root: Path = request.getfixturevalue("root")
        return dataclasses.replace(sample_config, root=root)
    return sample_config
