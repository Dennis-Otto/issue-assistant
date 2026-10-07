"""A sample repository with the issue assistant installed, as a git checkout."""

import os
import shutil
import subprocess
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
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """The sample repository after `install`, with every file tracked by git."""
    folder = tmp_path / "project"
    shutil.copytree(SAMPLE, folder)
    assistant.install(folder, REF, "v1.0.0")
    git(folder, "init", "-q", "-b", "main")
    git(folder, "add", "-A")
    git(folder, "commit", "-q", "-m", "sample")
    return folder


@pytest.fixture
def config(root: Path) -> assistant.Config:
    return assistant.load_config(root, REPOSITORY)
