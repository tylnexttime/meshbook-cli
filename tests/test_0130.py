"""meshbook-cli 0.13.0: the notification write verbs (meshbook DEV-DEBT §106).

Built on meshbook-sdk 0.4.3 (``client.notifications.mark_read`` /
``mark_unread`` / ``dismiss`` / ``read_all`` / ``summary``). The end-to-end
tests run against ``meshbook.testing.FakeNotificationServer``, the SDK's
stateful fake, in both of its modes: ``old`` (the production server on
2026-11-24, which answers ``updated: true`` whether or not a row matched)
and ``fixed`` (the §106 server branch). Each test that claims a write landed
checks the fake's row, not only the CLI's output.

Critique items (CRITIQUE-meshbook-106.md): C-02 (isRead is the read state),
C-04 (mark-all-read is a dry run by default), C-06 (exit 4), C-07 (the SDK
floor), C-12 (verb names, routing, opt-in --ids), C-13 (dismiss), C-16
(summary), C-20 (ids checked locally, every changed row printed).
"""
from __future__ import annotations

import re
from importlib.metadata import version
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


# ─── the dependency floor: 0.13.0 needs SDK 0.4.3 (C-07) ────────────────


def _sdk_specs() -> list[str]:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    return re.findall(r'"(meshbook-sdk(?:\[agent\])?[^"]*)"', text)


def test_every_sdk_requirement_has_the_043_floor():
    """The write verbs are SDK 0.4.3 surface: with the old floor, pip could
    resolve 0.4.2 and `mesh notifications mark-read` would die with an
    AttributeError. Runtime, [agent] and [dev] all say so."""
    specs = _sdk_specs()
    assert len(specs) == 3, specs
    for spec in specs:
        assert ">=0.4.3,<0.5" in spec, spec


def test_the_cli_docstring_states_the_same_floor():
    text = (ROOT / "mesh" / "cli.py").read_text(encoding="utf-8")
    assert "meshbook-sdk (>=0.4.3,<0.5)" in text


def test_the_installed_sdk_meets_the_043_floor():
    major, minor, patch = (int(x) for x in version("meshbook-sdk").split(".")[:3])
    assert (major, minor) == (0, 4) and patch >= 3
