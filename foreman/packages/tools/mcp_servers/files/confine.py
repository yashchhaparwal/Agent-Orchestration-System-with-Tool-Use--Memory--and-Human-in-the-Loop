"""Path confinement: every model-supplied path must resolve inside the workspace root.

Covers ``..``, absolute paths outside the root, symlinks that point outside (``resolve()`` follows
them), URL-encoded traversal, and NUL bytes. Never open the raw string.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote


class PathViolationError(ValueError):
    """Raised with a reason the model can act on."""


def resolve_within(root: Path, user_path: str) -> Path:
    if not isinstance(user_path, str) or not user_path.strip():
        raise PathViolationError("path is empty")
    if "\x00" in user_path:
        raise PathViolationError("path contains a NUL byte")
    decoded = unquote(user_path)
    if "%" in decoded:  # double-encoded traversal attempts
        decoded = unquote(decoded)
    candidate = Path(decoded)
    root_resolved = root.resolve()
    full = (candidate if candidate.is_absolute() else root_resolved / candidate).resolve()
    if full != root_resolved and not full.is_relative_to(root_resolved):
        raise PathViolationError(f"path escapes the workspace root: {user_path}")
    return full


def relative_to_root(root: Path, full: Path) -> str:
    rel = full.relative_to(root.resolve())
    return rel.as_posix() or "."
