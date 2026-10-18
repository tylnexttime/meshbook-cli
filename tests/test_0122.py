"""meshbook-cli 0.12.2: Alder's 0.12 field notes, and meshbook-sdk 0.4.2.

Field notes: E:/dev/meshbook-clients-review-2026-10-19/FIELD-NOTES-ALDER-0.12.md
(Alder tested 0.12.0 for real on a Windows bench, 2026-10-19). Each test
names the note (#N) or the SDK 0.4.2 surface it pins.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from importlib.metadata import version
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from meshbook import MeshbookError

from tests._fake import E1, MESH, U1, U2, Err, ok, ok_list

ROOT = Path(__file__).resolve().parent.parent
CAL = "/api/calendar-events"
SYD = ZoneInfo("Australia/Sydney")


# ─── the dependency floor: 0.12.2 needs SDK 0.4.2 ───────────────────────


def _sdk_specs() -> list[str]:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    return re.findall(r'"(meshbook-sdk(?:\[agent\])?[^"]*)"', text)


def test_every_sdk_requirement_has_the_042_floor():
    """The recurrence tzid, create(tz=), server_too_old and the pending-key
    verbs are SDK 0.4.2 surface: runtime, [agent] and [dev] all say so."""
    specs = _sdk_specs()
    assert len(specs) == 3, specs
    for spec in specs:
        assert ">=0.4.2,<0.5" in spec, spec


def test_the_installed_sdk_meets_the_042_floor():
    major, minor, patch = (int(x) for x in version("meshbook-sdk").split(".")[:3])
    assert (major, minor) == (0, 4) and patch >= 2


# ─── server_too_old: the SDK's hint, not a text match ───────────────────


def test_server_too_old_hint_is_used_whatever_the_message(capsys):
    """0.12.1 recognised an older server by FastAPI's exact "Not Found" text.
    SDK 0.4.2 says it with a hint (server_too_old); the CLI now follows the
    hint, so a reworded body still gets the advice."""
    from mesh._core import report_error

    e = MeshbookError("http_error", "No such route here", 404, hint="server_too_old")
    rc = report_error(e, as_json=True)
    assert rc == 1
    out = json.loads(capsys.readouterr().err.strip().splitlines()[-1])["error"]
    assert out["hintCode"] == "server_too_old"
    assert "older than meshbook-cli" in out["hint"]


def test_no_text_match_without_the_hint(capsys):
    """A bare "Not Found" WITHOUT the hint (the raw request() escape hatch, or a
    route outside the registry) proves nothing about the server's age."""
    from mesh._core import report_error

    report_error(MeshbookError("http_error", "Not Found", 404, hint="check_status"), as_json=True)
    out = json.loads(capsys.readouterr().err.strip().splitlines()[-1])["error"]
    assert "older than" not in (out["hint"] or "")
