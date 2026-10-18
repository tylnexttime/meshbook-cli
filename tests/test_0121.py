"""meshbook-cli 0.12.1: the CLI absorbs meshbook-sdk 0.4.1.

Each test here names the 0.12.0 behaviour it replaces. The SDK side of every
change is in meshbook-sdk's CHANGELOG 0.4.1.
"""
from __future__ import annotations

import re
from importlib.metadata import version
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


# ─── the dependency floor ────────────────────────────────────────────────


def _sdk_specs() -> list[str]:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    return re.findall(r'"(meshbook-sdk(?:\[agent\])?[^"]*)"', text)


def test_every_sdk_requirement_has_the_041_floor():
    """0.12.0 allowed meshbook-sdk 0.4.0, whose downloads could write outside
    their directory (C-03). Runtime, [agent] and [dev] must all say 0.4.1."""
    specs = _sdk_specs()
    assert len(specs) == 3, specs
    for spec in specs:
        assert ">=0.4.1,<0.5" in spec, spec


def test_the_installed_sdk_meets_the_floor():
    major, minor, patch = (int(x) for x in version("meshbook-sdk").split(".")[:3])
    assert (major, minor) == (0, 4) and patch >= 1
