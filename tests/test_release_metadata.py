from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RELEASE_VERSION = "0.4.1"


def _read(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


def test_release_version_is_consistent_across_public_surfaces() -> None:
    assert f'version = "{RELEASE_VERSION}"' in _read("pyproject.toml")
    assert f'__version__ = "{RELEASE_VERSION}"' in _read("src/oram/__init__.py")
    assert f"Current release: `{RELEASE_VERSION}`." in _read("README.md")
    assert f"ORAM `{RELEASE_VERSION}` is" in _read("README.md")
    assert f"project(ORAM_PLUGIN VERSION {RELEASE_VERSION})" in _read(
        "plugins/oram-plugin/CMakeLists.txt"
    )
    assert f"## v{RELEASE_VERSION}" in _read("CHANGELOG.md")
