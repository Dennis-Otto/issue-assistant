"""A sample repository with the issue assistant installed, as a git checkout."""

import shutil
import subprocess
from pathlib import Path

import pytest

import issue_assistant as assistant
from support import REF, REPOSITORY

SAMPLE = Path(__file__).parent / "sample"


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
