"""The CLI's version: pyproject.toml's, never a literal.

pyproject.toml is the single source. A hardcoded string sat at "0.6.0"
through two releases (DEV-DEBT §92), and the old test only checked the
string's SHAPE, which is how a stale 0.1.0 install passed it: the guarding
test now compares against pyproject.toml itself.

Installed package metadata is the source for a normal (wheel) install. From a
source checkout, which includes every editable install, pyproject.toml beside
the package wins. An editable install's metadata is written once, at install
time, and never follows a version bump: on 22 Oct 2026 the BGGR-K workspace
venv still reported 0.6.0 while running 0.12.3's code.
"""
from __future__ import annotations

import re
from pathlib import Path

_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _checkout_version() -> str | None:
    """pyproject.toml's version when this package sits in its own source checkout."""
    try:
        text = _PYPROJECT.read_text(encoding="utf-8")
    except OSError:
        return None
    if "[project]" not in text:
        return None
    project = text.split("[project]", 1)[1].split("\n[", 1)[0]
    if not re.search(r'^name\s*=\s*"meshbook-cli"\s*$', project, re.M):
        return None  # some other project's pyproject (e.g. a parent folder)
    m = re.search(r'^version\s*=\s*"([^"]+)"\s*$', project, re.M)
    return m.group(1) if m else None


def _installed_version() -> str:
    try:
        from importlib.metadata import version as _pkg_version

        return _pkg_version("meshbook-cli")
    except Exception:  # an uninstalled source checkout without a readable pyproject
        return "0.0.0+uninstalled"


VERSION = _checkout_version() or _installed_version()

__all__ = ["VERSION"]
