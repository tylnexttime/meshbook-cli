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


# ─── #1: a series carries its own time zone (recurrence tzid) ───────────


def _ev(**over):
    ev = {"id": E1, "title": "Weekly", "startAt": "2027-03-21T23:00:00+00:00",
          "endAt": "2027-03-22T00:00:00+00:00", "isAllDay": False, "eventType": "Meeting",
          "attendees": [], "recurrence": None}
    ev.update(over)
    return ev


def _weekly(tzid=..., **over):
    rec = {"frequency": "Weekly", "intervalN": 1, "untilDate": None, "countN": None}
    if tzid is not ...:
        rec["tzid"] = tzid
    rec.update(over)
    return rec


@pytest.fixture()
def machine_zone(monkeypatch):
    """Pin "this machine's IANA zone" (0.12.1's Windows mapping, $TZ, ...)."""
    from mesh import _when

    def pin(name):
        monkeypatch.setattr(_when, "iana_name", lambda tz: tz or name)
    pin("Europe/Warsaw")
    return pin


def test_create_repeat_with_tz_sends_the_series_tzid(api, run, machine_zone):
    """Alder #1: `--tz Australia/Sydney` on create changed nothing about the
    series; both stored identically and drifted to 09:00 after DST ended."""
    api.add("POST", CAL, ok(_ev(recurrence=_weekly("Australia/Sydney"))))
    r = run("events", "create", "--title", "Weekly", "--start", "2027-03-22 10:00",
            "--repeat", "weekly", "--tz", "Australia/Sydney", "--force")
    assert r.rc == 0, r.err
    body = api.body(api.one("POST", CAL))
    assert body["recurrence"]["tzid"] == "Australia/Sydney"
    assert body["startAt"] == "2027-03-22T10:00:00+11:00"
    assert "UTC wall clock" not in r.out
    assert "Australia/Sydney" in r.out.split("repeats:")[1].splitlines()[0]


def test_create_repeat_without_tz_sends_this_machines_zone(api, run, machine_zone):
    api.add("POST", CAL, ok(_ev(recurrence=_weekly("Europe/Warsaw"))))
    r = run("events", "create", "--title", "Weekly", "--start", "2027-03-22 10:00",
            "--repeat", "weekly", "--force")
    assert r.rc == 0, r.err
    assert api.body(api.one("POST", CAL))["recurrence"]["tzid"] == "Europe/Warsaw"
    assert "UTC wall clock" not in r.out


def test_create_repeat_with_no_known_zone_sends_none_and_says_utc(api, run, machine_zone):
    machine_zone(None)
    api.add("POST", CAL, ok(_ev(recurrence=_weekly(None))))
    r = run("events", "create", "--title", "Weekly", "--start", "2027-03-22 10:00",
            "--repeat", "weekly", "--force")
    assert r.rc == 0, r.err
    assert "tzid" not in api.body(api.one("POST", CAL))["recurrence"]
    assert "UTC wall clock" in r.out and "--tz" in r.out


def test_a_one_off_event_sends_no_tzid(api, run, machine_zone):
    api.add("POST", CAL, ok(_ev()))
    run("events", "create", "--title", "Once", "--start", "2027-03-22 10:00", "--force")
    assert "recurrence" not in api.body(api.one("POST", CAL))


def test_an_older_server_that_drops_tzid_is_called_out(api, run, machine_zone):
    """The deployed server ignored an unknown `tzid` key (pydantic extra=ignore):
    its recurrence output has no tzid at all. Say the zone was not stored."""
    api.add("POST", CAL, ok(_ev(recurrence=_weekly())))
    r = run("events", "create", "--title", "Weekly", "--start", "2027-03-22 10:00",
            "--repeat", "weekly", "--tz", "Australia/Sydney", "--force")
    assert r.rc == 0, r.err
    assert "did not store" in r.out and "UTC wall clock" in r.out


def test_update_repeat_sends_the_series_tzid(api, run, machine_zone):
    api.add("PATCH", f"{CAL}/{E1}", ok(_ev(recurrence=_weekly("Europe/Warsaw"))))
    r = run("events", "update", E1, "--repeat", "weekly")
    assert r.rc == 0, r.err
    assert api.body(api.one("PATCH", f"{CAL}/{E1}"))["recurrence"]["tzid"] == "Europe/Warsaw"
    assert "UTC wall clock" not in r.out


def test_update_repeat_with_tz_wins_over_the_machine(api, run, machine_zone):
    api.add("PATCH", f"{CAL}/{E1}", ok(_ev(recurrence=_weekly("Australia/Sydney"))))
    run("events", "update", E1, "--repeat", "weekly", "--tz", "Australia/Sydney")
    assert api.body(api.one("PATCH", f"{CAL}/{E1}"))["recurrence"]["tzid"] == "Australia/Sydney"


# ─── #9 + #1: the list's UTC wall clock note ────────────────────────────


def test_no_dst_note_when_there_are_no_events(api, run, machine_zone):
    """Alder #9: the note printed even over "(no events)"."""
    machine_zone(None)
    api.add("GET", CAL, ok_list([]))
    r = run("events", "list", "--today")
    assert r.rc == 0, r.err
    assert "UTC wall clock" not in r.out


def test_no_dst_note_when_every_series_has_its_own_zone(api, run, machine_zone):
    machine_zone(None)
    occ = _ev(occurrenceStart="2027-03-21T23:00:00+00:00",
              occurrenceEnd="2027-03-22T00:00:00+00:00", recurrence=_weekly("Australia/Sydney"))
    api.add("GET", CAL, ok_list([occ]))
    r = run("events", "list", "--from", "2027-03-21", "--to", "2027-03-23")
    assert "UTC wall clock" not in r.out


def test_dst_note_when_a_series_has_no_zone_and_the_reader_none(api, run, machine_zone):
    machine_zone(None)
    occ = _ev(occurrenceStart="2027-03-21T23:00:00+00:00",
              occurrenceEnd="2027-03-22T00:00:00+00:00", recurrence=_weekly(None))
    api.add("GET", CAL, ok_list([occ]))
    r = run("events", "list", "--from", "2027-03-21", "--to", "2027-03-23")
    assert "UTC wall clock" in r.out


# ─── #1b: the UTC offset label across a DST change ──────────────────────


def test_offset_is_shown_per_row_when_the_window_crosses_dst(api, run):
    """Alder #1b: the header said "Australia/Sydney, UTC+11:00" for a window
    crossing the 4 April 2027 DST end: one offset, computed once."""
    before = _ev(occurrenceStart="2027-03-28T23:00:00+00:00",
                 occurrenceEnd="2027-03-29T00:00:00+00:00", recurrence=_weekly("Australia/Sydney"))
    after = _ev(occurrenceStart="2027-04-05T00:00:00+00:00",
                occurrenceEnd="2027-04-05T01:00:00+00:00", recurrence=_weekly("Australia/Sydney"))
    api.add("GET", CAL, ok_list([before, after]))
    r = run("events", "list", "--from", "2027-03-29", "--to", "2027-04-06",
            "--tz", "Australia/Sydney")
    assert r.rc == 0, r.err
    header = r.out.splitlines()[0]
    assert "UTC+11:00)" not in header and "per row" in header
    rows = [ln for ln in r.out.splitlines() if "Weekly" in ln]
    assert "2027-03-29 10:00-11:00 UTC+11:00" in rows[0]
    assert "2027-04-05 10:00-11:00 UTC+10:00" in rows[1]


def test_one_offset_keeps_the_plain_header(api, run):
    occ = _ev(occurrenceStart="2027-03-21T23:00:00+00:00",
              occurrenceEnd="2027-03-22T00:00:00+00:00")
    api.add("GET", CAL, ok_list([occ]))
    r = run("events", "list", "--from", "2027-03-22", "--to", "2027-03-23",
            "--tz", "Australia/Sydney")
    assert "(times in Australia/Sydney, UTC+11:00" in r.out
    assert "10:00-11:00  Weekly" in r.out


def test_an_empty_window_across_dst_names_both_offsets(api, run):
    api.add("GET", CAL, ok_list([]))
    r = run("events", "list", "--from", "2027-03-29", "--to", "2027-04-06",
            "--tz", "Australia/Sydney")
    assert "UTC+11:00 -> UTC+10:00" in r.out
