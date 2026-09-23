from __future__ import annotations

import os
from pathlib import Path

import pytest

from packages.tools.mcp_servers.files.confine import PathViolationError, resolve_within


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "claims" / "CLM-1").mkdir(parents=True)
    (tmp_path / "claims" / "CLM-1" / "doc.md").write_text("hello", encoding="utf-8")
    (tmp_path.parent / "secret.txt").write_text("nope", encoding="utf-8")
    return tmp_path


def test_relative_path_inside_root(root: Path) -> None:
    assert resolve_within(root, "claims/CLM-1/doc.md") == (root / "claims/CLM-1/doc.md").resolve()


def test_root_itself(root: Path) -> None:
    assert resolve_within(root, ".") == root.resolve()


def test_absolute_path_inside_root_is_fine(root: Path) -> None:
    target = (root / "claims" / "CLM-1" / "doc.md").resolve()
    assert resolve_within(root, str(target)) == target


@pytest.mark.parametrize(
    "bad",
    [
        "../secret.txt",
        "claims/../../secret.txt",
        "%2e%2e/secret.txt",
        "%252e%252e/secret.txt",
        "claims/%2e%2e/%2e%2e/secret.txt",
        "",
        "   ",
        "claims/doc\x00.md",
    ],
)
def test_traversal_and_junk_rejected(root: Path, bad: str) -> None:
    with pytest.raises(PathViolationError):
        resolve_within(root, bad)


def test_absolute_path_outside_root_rejected(root: Path) -> None:
    with pytest.raises(PathViolationError):
        resolve_within(root, str((root.parent / "secret.txt").resolve()))


def test_symlink_escape_rejected(root: Path) -> None:
    link = root / "claims" / "escape"
    try:
        os.symlink(root.parent / "secret.txt", link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this platform/user")
    with pytest.raises(PathViolationError):
        resolve_within(root, "claims/escape")
