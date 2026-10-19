"""`mesh events` against CALENDAR-CONTRACT.md (2026-10-19), transport faked.

Times: a time typed without an offset is the machine's local zone (or --tz);
the expected values are computed the same way, so the suite passes in any zone.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from tests._fake import C1, E1, MESH, P1, U1, ok, ok_list

CAL = "/api/calendar-events"
SYD = ZoneInfo("Australia/Sydney")


def _local(y, mo, d, h=0, mi=0) -> dt.datetime:
    return dt.datetime(y, mo, d, h, mi).astimezone()


def _event(**over):
    ev = {"id": E1, "title": "Standup", "startAt": "2026-10-20T22:00:00+00:00",
          "endAt": "2026-10-20T23:00:00+00:00", "isAllDay": False, "eventType": "Meeting",
          "attendees": [], "recurrence": None}
    ev.update(over)
    return ev


def _same_instant(a: str, b: dt.datetime) -> bool:
    return dt.datetime.fromisoformat(a) == b


# ─── create ─────────────────────────────────────────────────────────

def test_create_converts_naive_time_to_local_and_says_so(api, run):
    api.add("GET", CAL, ok_list([]))
    api.add("POST", CAL, ok(_event()))
    r = run("events", "create", "--title", "Standup", "--start", "2026-10-21 09:00")
    assert r.rc == 0, r.err
    body = api.body(api.one("POST", CAL))
    start = _local(2026, 10, 21, 9)
    assert _same_instant(body["startAt"], start)
    assert dt.datetime.fromisoformat(body["startAt"]).utcoffset() is not None   # never naive
    assert _same_instant(body["endAt"], start + dt.timedelta(hours=1))         # default +1h
    assert body["eventType"] == "Meeting"
    assert "times in" in r.out and "UTC" in r.out
    assert f"mesh: Test Mesh ({MESH}) [config]" in r.out


def test_create_with_tz_uses_that_wall_clock(api, run):
    api.add("GET", CAL, ok_list([]))
    api.add("POST", CAL, ok(_event()))
    r = run("events", "create", "--title", "Standup", "--start", "2026-10-21 09:00",
            "--end", "2026-10-21 09:30", "--tz", "Australia/Sydney")
    assert r.rc == 0, r.err
    body = api.body(api.one("POST", CAL))
    assert body["startAt"] == "2026-10-21T09:00:00+11:00"
    assert body["endAt"] == "2026-10-21T09:30:00+11:00"
    assert "Australia/Sydney, UTC+11:00" in r.out


def test_create_keeps_an_explicit_offset(api, run):
    api.add("GET", CAL, ok_list([]))
    api.add("POST", CAL, ok(_event()))
    run("events", "create", "--title", "X", "--start", "2026-10-21T09:00:00+02:00")
    assert api.body(api.one("POST", CAL))["startAt"] == "2026-10-21T09:00:00+02:00"


def test_duplicate_guard_refuses_same_title_and_start(api, run):
    start = dt.datetime(2026, 10, 21, 9, tzinfo=SYD)
    api.add("GET", CAL, ok_list([_event(title="  standup ", startAt=start.isoformat())]))
    r = run("events", "create", "--title", "Standup", "--start", "2026-10-21 09:00",
            "--tz", "Australia/Sydney")
    assert r.rc == 1
    assert "already starts" in r.err and "--force" in r.err
    assert not api.calls("POST", CAL)


def test_force_skips_the_guard(api, run):
    api.add("POST", CAL, ok(_event()))
    r = run("events", "create", "--title", "Standup", "--start", "2026-10-21 09:00", "--force")
    assert r.rc == 0, r.err
    assert not api.calls("GET", CAL)          # no duplicate lookup at all
    assert api.calls("POST", CAL)


def test_create_series_attendees_and_link(api, run, monkeypatch):
    from mesh import _when

    monkeypatch.setattr(_when, "iana_name", lambda tz: tz or "Europe/Warsaw")
    api.add("GET", CAL, ok_list([]))
    api.add("GET", "/api/users", ok_list([{"id": U1, "username": "rook", "displayName": "Rook"}]))
    api.add("GET", "/api/contacts", ok_list([{"id": C1, "displayName": "Jane Doe"}]))
    api.add("POST", CAL, ok(_event(recurrence={"frequency": "Weekly", "intervalN": 2,
                                               "untilDate": "2026-12-31"})))
    r = run("events", "create", "--title", "Review", "--start", "2026-10-21 09:00",
            "--repeat", "fortnightly", "--until", "2026-12-31",
            "--attendee", "user:rook", "--attendee", "contact:Jane Doe",
            "--link", f"project:{P1}", "--type", "call", "--location", "Room 1")
    assert r.rc == 0, r.err
    body = api.body(api.one("POST", CAL))
    assert body["recurrence"] == {"frequency": "Weekly", "intervalN": 2, "untilDate": "2026-12-31",
                                  "tzid": "Europe/Warsaw"}   # 0.12.2: the machine zone (#1)
    assert body["attendees"] == [{"userId": U1}, {"contactId": C1}]
    assert (body["linkedEntityType"], body["linkedEntityId"]) == ("project", P1)
    assert body["eventType"] == "Call"         # case-insensitive flag, canonical on the wire
    assert body["location"] == "Room 1"
    assert "repeats: fortnightly until 2026-12-31" in r.out


def test_create_count_instead_of_until(api, run):
    api.add("POST", CAL, ok(_event()))
    run("events", "create", "--title", "X", "--start", "2026-10-21 09:00", "--repeat", "daily",
        "--count", "5", "--force")
    rec = api.body(api.one("POST", CAL))["recurrence"]
    assert rec["frequency"] == "Daily" and rec["countN"] == 5 and rec["untilDate"] is None


def test_create_all_day_from_a_date(api, run):
    api.add("POST", CAL, ok(_event(isAllDay=True)))
    r = run("events", "create", "--title", "Offsite", "--start", "2026-10-23", "--all-day", "--force")
    assert r.rc == 0, r.err
    body = api.body(api.one("POST", CAL))
    assert body["isAllDay"] is True
    assert _same_instant(body["startAt"], _local(2026, 10, 23))
    assert _same_instant(body["endAt"], _local(2026, 10, 24))


@pytest.mark.parametrize("extra,needle", [
    ([], "--all-day"),                                  # a bare date needs --all-day
    (["--until", "2026-12-31"], "--repeat"),            # --until alone
    (["--repeat", "weekly", "--until", "2026-12-31", "--count", "3"], "not both"),
    (["--link", "project"], "TYPE:ID"),
])
def test_create_refuses_bad_flags_locally(api, run, extra, needle):
    start = "2026-10-21" if not extra else "2026-10-21 09:00"
    r = run("events", "create", "--title", "X", "--start", start, "--force", *extra)
    assert r.rc == 2
    assert needle in r.err
    assert not [q for q in api.requests if q.get_method() == "POST"]


def test_bad_time_is_a_clear_error(api, run):
    r = run("--json", "events", "create", "--title", "X", "--start", "next tuesday-ish")
    assert r.rc == 2
    assert "YYYY-MM-DD" in r.err_json()["error"]["message"]


# ─── update / show / delete ─────────────────────────────────────────

def test_update_sends_only_given_fields(api, run):
    api.add("PATCH", f"{CAL}/{E1}", ok(_event(title="Moved")))
    r = run("events", "update", E1, "--start", "2026-10-22 10:00", "--location", "",
            "--no-repeat", "--tz", "Australia/Sydney")
    assert r.rc == 0, r.err
    body = api.body(api.one("PATCH", f"{CAL}/{E1}"))
    assert body == {"startAt": "2026-10-22T10:00:00+11:00", "location": None, "recurrence": None}
    assert "repeats: no" in r.out


def test_update_attendees_replace_and_repeat(api, run, monkeypatch):
    from mesh import _when

    monkeypatch.setattr(_when, "iana_name", lambda tz: tz or "Europe/Warsaw")
    api.add("GET", "/api/users", ok_list([{"id": U1, "username": "rook"}]))
    api.add("PATCH", f"{CAL}/{E1}", ok(_event(recurrence={"frequency": "Monthly", "intervalN": 1})))
    run("events", "update", E1, "--attendee", "user:@rook", "--repeat", "monthly", "--no-all-day")
    body = api.body(api.one("PATCH", f"{CAL}/{E1}"))
    assert body["attendees"] == [{"userId": U1}]
    assert body["recurrence"] == {"frequency": "Monthly", "intervalN": 1, "untilDate": None,
                                  "tzid": "Europe/Warsaw"}
    assert body["isAllDay"] is False


def test_update_repeat_and_no_repeat_conflict(api, run):
    r = run("events", "update", E1, "--repeat", "weekly", "--no-repeat")
    assert r.rc == 2
    assert not api.calls("PATCH", f"{CAL}/{E1}")


def test_update_with_nothing_to_change_is_refused(api, run):
    r = run("events", "update", E1)
    assert r.rc == 2
    assert "Nothing to update" in r.err


def test_show_lists_attendees_and_series(api, run):
    api.add("GET", f"{CAL}/{E1}", ok(_event(
        location="Room 1",
        recurrence={"frequency": "Weekly", "intervalN": 1, "untilDate": "2026-12-31"},
        attendees=[{"id": "at1", "userDisplayName": "Rook", "response": "Accepted",
                    "isOrganiser": True}])))
    r = run("events", "show", E1)
    assert r.rc == 0, r.err
    assert "Rook  Accepted (organiser)" in r.out
    assert "repeats:   weekly until 2026-12-31" in r.out
    assert "Room 1" in r.out


def test_delete_by_id_only(api, run):
    api.add("DELETE", f"{CAL}/{E1}", ok({"id": E1, "deleted": True}))
    assert run("events", "delete", E1).rc == 0
    assert api.one("DELETE", f"{CAL}/{E1}")
    r = run("events", "delete", "Standup")
    assert r.rc == 2 and "UUID" in r.err


# ─── list windows and expansion ─────────────────────────────────────

def test_list_today_window_expands_by_default(api, run):
    api.add("GET", CAL, ok_list([]))
    r = run("events", "list", "--today", "--tz", "Australia/Sydney")
    assert r.rc == 0, r.err
    q = api.query(api.one("GET", CAL))
    today = dt.datetime.now(SYD).date()
    assert q["from"] == dt.datetime(today.year, today.month, today.day, tzinfo=SYD).isoformat()
    nxt = today + dt.timedelta(days=1)
    assert q["to"] == dt.datetime(nxt.year, nxt.month, nxt.day, tzinfo=SYD).isoformat()
    assert q["expand"] == "true"
    assert q["tz"] == "Australia/Sydney"
    assert "occurrences expanded" in r.out


def test_list_week_is_seven_days(api, run):
    api.add("GET", CAL, ok_list([]))
    run("events", "list", "--week")
    q = api.query(api.one("GET", CAL))
    span = dt.datetime.fromisoformat(q["to"]) - dt.datetime.fromisoformat(q["from"])
    assert dt.timedelta(days=6, hours=22) <= span <= dt.timedelta(days=7, hours=2)  # DST-safe
    assert q["expand"] == "true"


def test_list_to_date_includes_that_day(api, run):
    api.add("GET", CAL, ok_list([]))
    run("events", "list", "--from", "2026-10-20", "--to", "2026-10-23", "--tz", "Australia/Sydney")
    q = api.query(api.one("GET", CAL))
    assert q["from"] == "2026-10-20T00:00:00+11:00"
    assert q["to"] == "2026-10-24T00:00:00+11:00"


def test_list_without_window_does_not_expand(api, run):
    api.add("GET", CAL, ok_list([_event()]))
    r = run("events", "list")
    assert r.rc == 0, r.err
    q = api.query(api.one("GET", CAL))
    assert "expand" not in q and "from" not in q and "tz" not in q
    assert "Standup" in r.out and "all dates" in r.out


def test_list_no_expand_with_window(api, run):
    api.add("GET", CAL, ok_list([]))
    run("events", "list", "--today", "--no-expand")
    assert "expand" not in api.query(api.one("GET", CAL))


def test_list_expand_needs_a_window(api, run):
    r = run("events", "list", "--expand")
    assert r.rc == 2
    assert not api.calls("GET", CAL)


def test_list_without_known_iana_zone_says_utc_wall_clock(api, run, monkeypatch):
    from mesh import _when

    monkeypatch.setattr(_when, "iana_name", lambda tz: None)
    # 0.12.2: only over a series that has no zone of its own (and never over
    # an empty listing, Alder #9).
    occ = _event(occurrenceStart="2026-10-20T22:00:00+00:00",
                 occurrenceEnd="2026-10-20T23:00:00+00:00",
                 recurrence={"frequency": "Weekly", "intervalN": 1})
    api.add("GET", CAL, ok_list([occ]))
    r = run("events", "list", "--today")
    assert "tz" not in api.query(api.one("GET", CAL))
    assert "UTC wall clock" in r.out


def test_list_linked_and_owner_filters(api, run):
    api.add("GET", CAL, ok_list([]))
    run("events", "list", "--linked", f"project:{P1}", "--owner", U1)
    q = api.query(api.one("GET", CAL))
    assert (q["linkedType"], q["linkedId"], q["ownerId"]) == ("project", P1, U1)


def test_list_renders_occurrences_on_the_display_clock(api, run):
    occ = _event(occurrenceStart="2026-10-20T22:00:00+00:00",
                 occurrenceEnd="2026-10-20T23:00:00+00:00", seriesId=E1,
                 recurrence={"frequency": "Weekly", "intervalN": 1})
    api.add("GET", CAL, ok_list([occ], total=1, truncated=True))
    r = run("events", "list", "--today", "--tz", "Australia/Sydney")
    assert "Wed 2026-10-21 09:00-10:00  Standup  [Meeting]  (repeats weekly)" in r.out
    assert "capped this window at 500" in r.out
    env = run("--json", "events", "list", "--today", "--tz", "Australia/Sydney").json()
    assert env["truncated"] is True and env["expanded"] is True and env["count"] == 1
