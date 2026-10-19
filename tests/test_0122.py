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


# ─── #2: `events update --start` keeps the duration ─────────────────────


def _syd(y, mo, d, h=0, mi=0) -> str:
    return dt.datetime(y, mo, d, h, mi, tzinfo=SYD).isoformat()


def _patch_body(api):
    return api.body(api.one("PATCH", f"{CAL}/{E1}"))


def test_update_start_alone_keeps_the_duration(api, run):
    """Alder #2: 10:00-11:00 moved with --start 11:00 became 11:00-11:00."""
    api.add("GET", f"{CAL}/{E1}", ok(_ev(startAt=_syd(2026, 10, 22, 10), endAt=_syd(2026, 10, 22, 11))))
    api.add("PATCH", f"{CAL}/{E1}", ok(_ev(startAt=_syd(2026, 10, 22, 11), endAt=_syd(2026, 10, 22, 12))))
    r = run("events", "update", E1, "--start", "2026-10-22 11:00", "--tz", "Australia/Sydney")
    assert r.rc == 0, r.err
    body = _patch_body(api)
    assert body["startAt"] == "2026-10-22T11:00:00+11:00"
    assert body["endAt"] == "2026-10-22T12:00:00+11:00"
    assert "duration kept" in r.out


def test_the_duration_is_kept_across_a_dst_change(api, run):
    """A 90-minute meeting stays 90 minutes when moved past the DST end."""
    api.add("GET", f"{CAL}/{E1}", ok(_ev(startAt=_syd(2027, 4, 3, 10), endAt=_syd(2027, 4, 3, 11, 30))))
    api.add("PATCH", f"{CAL}/{E1}", ok(_ev()))
    run("events", "update", E1, "--start", "2027-04-10 10:00", "--tz", "Australia/Sydney")
    body = _patch_body(api)
    assert body["startAt"] == "2027-04-10T10:00:00+10:00"
    assert body["endAt"] == "2027-04-10T11:30:00+10:00"


def test_an_all_day_event_keeps_its_day_count(api, run):
    """2 days stays 2 days, counted in days: 49 absolute hours across the DST
    end would land the end at 01:00, not midnight. A bare date is fine on an
    event that is already all-day (no --all-day needed)."""
    api.add("GET", f"{CAL}/{E1}", ok(_ev(isAllDay=True, startAt=_syd(2027, 4, 3),
                                         endAt=_syd(2027, 4, 5))))
    api.add("PATCH", f"{CAL}/{E1}", ok(_ev(isAllDay=True)))
    r = run("events", "update", E1, "--start", "2027-04-01", "--tz", "Australia/Sydney")
    assert r.rc == 0, r.err
    body = _patch_body(api)
    assert body["startAt"] == "2027-04-01T00:00:00+11:00"
    assert body["endAt"] == "2027-04-03T00:00:00+11:00"
    assert "2 day" in r.out


def test_all_day_day_count_across_dst(api, run):
    api.add("GET", f"{CAL}/{E1}", ok(_ev(isAllDay=True, startAt=_syd(2027, 4, 3),
                                         endAt=_syd(2027, 4, 5))))
    api.add("PATCH", f"{CAL}/{E1}", ok(_ev(isAllDay=True)))
    run("events", "update", E1, "--start", "2027-04-10", "--tz", "Australia/Sydney")
    assert _patch_body(api)["endAt"] == "2027-04-12T00:00:00+10:00"


def test_start_and_end_together_need_no_lookup(api, run):
    api.add("PATCH", f"{CAL}/{E1}", ok(_ev()))
    r = run("events", "update", E1, "--start", "2026-10-22 11:00", "--end", "2026-10-22 11:15",
            "--tz", "Australia/Sydney")
    assert r.rc == 0, r.err
    assert not api.calls("GET", f"{CAL}/{E1}")
    assert _patch_body(api)["endAt"] == "2026-10-22T11:15:00+11:00"


def test_end_alone_is_an_explicit_end(api, run):
    api.add("PATCH", f"{CAL}/{E1}", ok(_ev()))
    run("events", "update", E1, "--end", "2026-10-22 13:00", "--tz", "Australia/Sydney")
    assert not api.calls("GET", f"{CAL}/{E1}")
    assert _patch_body(api) == {"endAt": "2026-10-22T13:00:00+11:00"}


def test_a_zero_length_meeting_gets_the_default_hour(api, run):
    """A row 0.12.0 already broke (start == end) is repaired, not copied: the
    2026-10-19 server refuses a zero-length Meeting (422 invalid_time_range)."""
    api.add("GET", f"{CAL}/{E1}", ok(_ev(startAt=_syd(2026, 10, 22, 11), endAt=_syd(2026, 10, 22, 11))))
    api.add("PATCH", f"{CAL}/{E1}", ok(_ev()))
    run("events", "update", E1, "--start", "2026-10-22 14:00", "--tz", "Australia/Sydney")
    assert _patch_body(api)["endAt"] == "2026-10-22T15:00:00+11:00"


def test_a_deadline_stays_a_point_in_time(api, run):
    api.add("GET", f"{CAL}/{E1}", ok(_ev(eventType="Deadline", startAt=_syd(2026, 10, 22, 17),
                                         endAt=_syd(2026, 10, 22, 17))))
    api.add("PATCH", f"{CAL}/{E1}", ok(_ev()))
    run("events", "update", E1, "--start", "2026-10-23 17:00", "--tz", "Australia/Sydney")
    body = _patch_body(api)
    assert body["endAt"] == body["startAt"] == "2026-10-23T17:00:00+11:00"


def test_making_a_timed_event_all_day_gives_one_day(api, run):
    api.add("GET", f"{CAL}/{E1}", ok(_ev(startAt=_syd(2026, 10, 22, 10), endAt=_syd(2026, 10, 22, 11))))
    api.add("PATCH", f"{CAL}/{E1}", ok(_ev(isAllDay=True)))
    run("events", "update", E1, "--start", "2026-10-23", "--all-day", "--tz", "Australia/Sydney")
    body = _patch_body(api)
    assert (body["startAt"], body["endAt"]) == ("2026-10-23T00:00:00+11:00", "2026-10-24T00:00:00+11:00")


# ─── #10: the zone label survives an update that sends no time ──────────


def test_attendee_only_update_still_names_the_zone(api, run):
    """Alder #10: "when: ... 11:00-11:00" with no zone after an attendee-only update."""
    api.add("GET", "/api/users", ok_list([{"id": U1, "username": "rook"}]))
    api.add("PATCH", f"{CAL}/{E1}", ok(_ev(startAt=_syd(2026, 10, 22, 10), endAt=_syd(2026, 10, 22, 11))))
    r = run("events", "update", E1, "--attendee", "user:@rook", "--tz", "Australia/Sydney")
    assert r.rc == 0, r.err
    when = next(ln for ln in r.out.splitlines() if "when:" in ln)
    assert "10:00-11:00" in when and "(times in Australia/Sydney, UTC+11:00)" in when


# ─── #3: tasks create --assignee ────────────────────────────────────────

from tests._fake import P1, T1  # noqa: E402

ALDER = {"id": U1, "username": "alder", "displayName": "Alder"}


def _task(**over):
    # The deployed (pre-round-3) server's create answer: the scalar assignee IS
    # stored, but `assignees` / `assigneeCount` read only lead + task_assignees.
    t = {"id": T1, "title": "Probe", "status": "NotStarted", "projectId": P1,
         "assigneeUserId": U1, "assignees": [], "assigneeCount": 0}
    t.update(over)
    return t


def _task_routes(api, created):
    api.add("GET", "/api/users", ok_list([ALDER]))
    api.add("GET", "/api/projects", ok_list([{"id": P1, "name": "Ops", "code": "OPS"}]))
    api.add("POST", "/api/tasks", ok(created))


def test_create_assignee_is_sent_and_shown(api, run):
    """Alder #3: the CLI sent assigneeUserId and the server stored it, but the
    human output never said so, and the JSON's `assignees` read []."""
    _task_routes(api, _task())
    r = run("tasks", "create", "--project", P1, "--title", "Probe", "--assignee", "@alder")
    assert r.rc == 0, r.err
    assert api.body(api.one("POST", "/api/tasks"))["assigneeUserId"] == U1
    assert "assignee: Alder (@alder)" in r.out


def test_create_refuses_loudly_when_the_assignee_was_not_stored(api, run):
    _task_routes(api, _task(assigneeUserId=None))
    r = run("tasks", "create", "--project", P1, "--title", "Probe", "--assignee", "@alder")
    assert r.rc == 1
    assert "did not store the assignee" in r.err and T1 in r.err
    e = run("--json", "tasks", "create", "--project", P1, "--title", "Probe",
            "--assignee", "@alder").err_json()["error"]
    assert e["code"] == "assignee_not_stored"


def test_create_refuses_when_someone_else_was_stored(api, run):
    _task_routes(api, _task(assigneeUserId=U2))
    r = run("tasks", "create", "--project", P1, "--title", "Probe", "--assignee", "@alder")
    assert r.rc == 1 and "did not store the assignee" in r.err


def test_update_assignee_is_checked_too(api, run):
    api.add("GET", "/api/users", ok_list([ALDER]))
    api.add("PATCH", f"/api/tasks/{T1}", ok(_task(assigneeUserId=None)))
    r = run("tasks", "update", T1, "--assignee", "alder")
    assert r.rc == 1 and "did not store the assignee" in r.err


def test_update_assignee_success_says_who(api, run):
    api.add("GET", "/api/users", ok_list([ALDER]))
    api.add("PATCH", f"/api/tasks/{T1}", ok(_task()))
    r = run("tasks", "update", T1, "--assignee", "alder")
    assert r.rc == 0, r.err
    assert "assignee: Alder (@alder)" in r.out


def test_show_names_the_scalar_assignee_beside_the_lead(api, run):
    """Squashed with #3: `tasks show` printed `assignees` (the lead, on the
    deployed server) and hid the scalar assignee whenever that list was non-empty."""
    api.add("GET", f"/api/tasks/{T1}", ok(_task(assignees=[{"userId": U2, "displayName": "Lead"}])))
    r = run("tasks", "show", T1)
    assert r.rc == 0, r.err
    line = next(ln for ln in r.out.splitlines() if "assignee" in ln)
    assert "Lead" in line and U1 in line
