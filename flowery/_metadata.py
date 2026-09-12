"""flowery - when girls meet each others."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

__name__ = "flowery"
__author__ = "noaione"
__author_email__ = "noaione@n4o.xyz"
__license__ = "MIT"
__copyright__ = "Copyright (c) 2026-present noaione"


def _resolve_version(distribution: str = "flowery") -> str:
    """Read the version from the installed distribution.

    ``pyproject.toml`` is the single source of truth. This falls back to a
    placeholder when the package is imported straight from a source tree that
    was never installed.
    """
    try:
        return version(distribution)
    except PackageNotFoundError:
        return "0.0.0+unknown"


__version__ = _resolve_version()

__all__ = ("__author__", "__copyright__", "__license__", "__name__", "__version__")
