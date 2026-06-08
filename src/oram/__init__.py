"""oram — a speech-operated terminal looper for synthetic sound studies."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("oram")
except PackageNotFoundError:
    __version__ = "0.1"
