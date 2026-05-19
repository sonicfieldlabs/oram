"""oram.archive.safety — path-segment sanitization for archive paths.

prevents path-traversal attacks in session IDs, layer names, and
export paths by enforcing a strict allowlist of characters.
"""

from __future__ import annotations

import re
from pathlib import Path

# only allow alphanumeric, dot, underscore, hyphen — max 64 chars
_SAFE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def safe_segment(name: str, fallback: str = "untitled") -> str:
    """validate a path segment (session ID, layer name, etc.).

    returns the name unchanged if it matches the safe pattern,
    otherwise returns the fallback.
    """
    name = name.strip()
    return name if _SAFE.match(name) else fallback


def validate_export_path(path: Path, session_dir: Path | None = None) -> Path:
    """validate that an export path is within an allowed directory.

    the path must resolve to a location under either the current
    working directory or the configured session directory.

    raises ValueError if the path escapes both.
    """
    resolved = path.resolve()
    cwd = Path.cwd().resolve()

    allowed = [cwd]
    if session_dir is not None:
        allowed.append(session_dir.resolve())

    for base in allowed:
        try:
            resolved.relative_to(base)
            return resolved
        except ValueError:
            continue

    raise ValueError(
        f"export path '{resolved}' is outside allowed directories: "
        f"{[str(b) for b in allowed]}"
    )
