"""The CLI's version: installed package metadata, never a literal.

pyproject.toml is the single source. A hardcoded string sat at "0.6.0"
through two releases (DEV-DEBT §92), and the old test only checked the
string's SHAPE, which is how a stale 0.1.0 install passed it: the guarding
test now compares against pyproject.toml itself.
"""
from __future__ import annotations

try:
    from importlib.metadata import version as _pkg_version

    VERSION = _pkg_version("meshbook-cli")
except Exception:  # an uninstalled source checkout only
    VERSION = "0.0.0+uninstalled"

__all__ = ["VERSION"]
