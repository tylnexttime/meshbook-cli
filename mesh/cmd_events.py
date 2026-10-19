"""`mesh events`: the app-native mesh calendar (CALENDAR-CONTRACT.md, 2026-10-19).

Times typed without an offset are on this machine's clock (or ``--tz``), and
every human line says which zone was used; the SDK itself never guesses.
"""
from __future__ import annotations

import argparse
import datetime as _dt
from typing import Any

from meshbook import UNSET
from meshbook.resources import EVENT_TYPES

from . import _when
from ._core import CliError, Ctx, need_uuid

REPEATS = ("daily", "weekly", "fortnightly", "monthly", "yearly")


def _link(value: str, flag: str) -> tuple[str, str]:
    kind, sep, ident = (value or "").partition(":")
    if not sep or not kind.strip() or not ident.strip():
        raise CliError("bad_argument", f"{flag} must look like TYPE:ID (e.g. project:<uuid>).")
    return kind.strip().lower(), ident.strip()


def _recurrence(a: Any) -> Any:
    """--repeat / --until / --count / --no-repeat → the SDK's recurrence value
    (UNSET = leave alone, None = clear, dict = set)."""
    if getattr(a, "no_repeat", False):
        if a.repeat or a.until or a.count:
            raise CliError("bad_argument", "--no-repeat cannot be combined with --repeat/--until/--count.")
        return None
    if not a.repeat:
        if a.until or a.count:
            raise CliError("bad_argument", "--until/--count need --repeat.")
        return UNSET
    if a.until and a.count:
        raise CliError("bad_argument", "Pass --until or --count, not both.")
    rule: dict[str, Any] = {"frequency": a.repeat}
    if a.until:
        rule["until"] = _when.parse_date(a.until, "until")
    if a.count:
        rule["count"] = a.count
    return rule


def _repeat_text(rec: Any) -> str:
    if not isinstance(rec, dict) or not rec.get("frequency"):
        return ""
    n = rec.get("intervalN") or 1
    freq = str(rec["frequency"]).lower()
    text = freq if n == 1 else ("fortnightly" if (freq == "weekly" and n == 2) else f"every {n} x {freq}")
    if rec.get("untilDate"):
        text += f" until {rec['untilDate']}"
    elif rec.get("countN"):
        text += f", {rec['countN']} times"
    return text


def _when_text(ev: dict[str, Any], tz: _dt.tzinfo | None) -> str:
    start = ev.get("occurrenceStart") or ev.get("startAt")
    end = ev.get("occurrenceEnd") or ev.get("endAt")
    if ev.get("isAllDay"):
        return f"{_when.show(start, tz, '%a %Y-%m-%d')} (all day)"
    first = _when.show(start, tz)
    last = _when.show(end, tz, "%H:%M")
    if _when.show(start, tz, "%Y-%m-%d") != _when.show(end, tz, "%Y-%m-%d"):
        last = _when.show(end, tz)
    return f"{first}-{last}"


def _start_of(ev: dict[str, Any]) -> Any:
    return ev.get("occurrenceStart") or ev.get("startAt")


def _event_line(tz: _dt.tzinfo | None, *, offset_per_row: bool = False) -> Any:
    """One listing row. *offset_per_row*: the rows do not share one UTC offset
    (the window crosses a DST change), so each timed row says its own."""
    def row(ev: dict[str, Any]) -> str:
        rep = _repeat_text(ev.get("recurrence"))
        rep = f"  (repeats {rep})" if rep else ""
        loc = f"  @ {ev.get('location')}" if ev.get("location") else ""
        when = _when_text(ev, tz)
        if offset_per_row and not ev.get("isAllDay"):
            off = _when.offset_at(_start_of(ev), tz)
            if off is not None:
                when += f" {_when.offset_text(off)}"
        return (f"  {when}  {ev.get('title')}  [{ev.get('eventType') or '?'}]"
                f"{loc}{rep}   {ev.get('id')}")
    return row


def _is_series(ev: dict[str, Any]) -> bool:
    rec = ev.get("recurrence")
    return isinstance(rec, dict) and bool(rec.get("frequency"))


def _list_zone_label(
    tz: _dt.tzinfo | None, start: Any, end: Any, items: list[Any]
) -> tuple[str, bool]:
    """(the header's zone label, show the offset per row?).

    0.12.1 computed one offset at the window's start, so a window crossing a
    DST change said "UTC+11:00" over rows that were at +10:00 (Alder #1b).
    Now: the rows' own offsets decide; one offset keeps the plain label, more
    than one moves the offset onto each row. An empty window names the
    offsets at both ends when they differ.
    """
    if items:
        offsets = {_when.offset_at(_start_of(ev), tz) for ev in items if not ev.get("isAllDay")}
        offsets.discard(None)
        if len(offsets) > 1:
            return f"{_when.zone_name(tz, start)}, UTC offset per row", True
        if offsets:
            when = next(_start_of(ev) for ev in items
                        if not ev.get("isAllDay") and _when.offset_at(_start_of(ev), tz) is not None)
            return _when.zone_label(tz, _when.as_aware(when)), False
        return _when.zone_label(tz, start), False
    if start is not None and end is not None:
        first, last = _when.offset_at(start, tz), _when.offset_at(end, tz)
        if first is not None and last is not None and first != last:
            return (f"{_when.zone_name(tz, start)}, {_when.offset_text(first)} -> "
                    f"{_when.offset_text(last)}"), False
    return _when.zone_label(tz, start), False


def _series_zone_lines(data: dict[str, Any], sent_tzid: str | None, *, sent_rule: bool) -> list[str]:
    """What the server did with the series' time zone, in one or two lines.

    * stored: ``repeats: weekly (wall clock: Australia/Sydney)``;
    * sent but the server's recurrence has no ``tzid`` at all: a server from
      before 2026-10-19 dropped it (pydantic ignores unknown keys), so say so;
    * nothing known to send (an unmapped Windows zone, no --tz): the series
      repeats on the UTC wall clock, and --tz is the fix.
    """
    rec = data.get("recurrence")
    rep = _repeat_text(rec)
    if not rep:
        return []
    stored = rec.get("tzid") if isinstance(rec, dict) else None
    if stored:
        return [f"  repeats: {rep} (wall clock: {stored})"]
    lines = [f"  repeats: {rep}"]
    if sent_rule and sent_tzid:
        lines.append(f"  note: this server did not store the series zone ({sent_tzid}); until it "
                     "is upgraded, occurrences repeat on the UTC wall clock and drift an hour "
                     "across DST")
    elif sent_rule:
        lines.append("  note: no IANA zone is known for this machine, so the series repeats on "
                     "the UTC wall clock; pass --tz Area/City to keep its local time across DST")
    elif isinstance(rec, dict) and "tzid" in rec:
        lines.append("  note: this series has no time zone (it repeats on the UTC wall clock); "
                     "give it one with --repeat FREQ --tz Area/City")
    return lines


# ─── verbs ──────────────────────────────────────────────────────────────


def cmd_events_list(ctx: Ctx) -> int:
    a = ctx.args
    tz = _when.zone(a.tz)
    start = end = None
    if a.today or a.week:
        if a.start or a.end:
            raise CliError("bad_argument", "--today/--week cannot be combined with --from/--to.")
        start = _when.midnight(_when.today(tz), tz)
        end = _when.midnight(_when.today(tz) + _dt.timedelta(days=7 if a.week else 1), tz)
    else:
        if a.start:
            start, _ = _when.parse_when(a.start, tz=tz, field="from")
        if a.end:
            end, _ = _when.parse_when(a.end, tz=tz, end_of_day=True, field="to")
    window = start is not None and end is not None
    expand = window if a.expand is None else a.expand
    if expand and not window:
        raise CliError("bad_argument", "--expand needs a window: --today, --week, or --from and --to.")
    linked_type = linked_id = None
    if a.linked:
        linked_type, linked_id = _link(a.linked, "--linked")
    iana = _when.iana_name(a.tz)
    rows = ctx.client.events.list(
        start, end, linked_type=linked_type, linked_id=linked_id, owner=a.owner,
        expand=expand, tz=iana if expand else None, limit=a.limit, offset=a.offset,
    )
    header = None
    per_row = False
    if not ctx.json:
        items = list(rows)
        zl, per_row = _list_zone_label(tz, start, end, items)
        span = (f"{_when.show(start.isoformat(), tz, '%Y-%m-%d %H:%M')} -> "
                f"{_when.show(end.isoformat(), tz, '%Y-%m-%d %H:%M')}") if window else "all dates"
        header = f"  Events {span}  (times in {zl}"
        header += ", occurrences expanded" if expand else ""
        header += ")"
        # Only when it is true of a row on screen (Alder #9: it printed over
        # "(no events)"): a series without its own tzid, expanded with no
        # reader zone, repeats on the UTC wall clock.
        if expand and not iana and any(
            _is_series(ev) and not ev["recurrence"].get("tzid") for ev in items
        ):
            header += ("\n  note: a series without its own time zone repeats on the UTC wall "
                       "clock; pass --tz Area/City to keep local times across DST")
        if rows.truncated:
            header += "\n  note: the server capped this window at 500 occurrences (narrow it)"
    extra = {"truncated": rows.truncated, "expanded": expand, "tz": iana}
    return ctx.listing(rows, _event_line(tz, offset_per_row=per_row), header=header,
                       empty="  (no events)", extra=extra)


def cmd_events_show(ctx: Ctx) -> int:
    a = ctx.args
    tz = _when.zone(a.tz)
    ev = ctx.client.events.get(a.event_id)
    if ctx.json:
        ctx.emit(ev)
        return 0
    print(f"{ev.get('title')}   {ev.get('id')}")
    print(f"  when:      {_when_text(ev, tz)}  ({_when.zone_label(tz)})")
    print(f"  type:      {ev.get('eventType')}")
    for label, key in (("location", "location"), ("url", "meetingUrl"), ("owner", "ownerName"),
                       ("color", "colorHex")):
        if ev.get(key):
            print(f"  {label + ':':<11}{ev[key]}")
    if ev.get("linkedEntityType"):
        name = ev.get("linkedEntityName") or ev.get("linkedEntityId")
        print(f"  linked:    {ev.get('linkedEntityType')}:{name}")
    rep = _repeat_text(ev.get("recurrence"))
    if rep:
        print(f"  repeats:   {rep}")
    attendees = ev.get("attendees") or []
    if attendees:
        print(f"  attendees ({len(attendees)}):")
        for at in attendees:
            who = (at.get("userDisplayName") or at.get("contactDisplayName")
                   or at.get("externalName") or at.get("externalEmail") or "?")
            flags = " (organiser)" if at.get("isOrganiser") else ""
            flags += " (optional)" if at.get("isOptional") else ""
            print(f"    {who}  {at.get('response') or ''}{flags}   {at.get('id')}")
    if ev.get("description"):
        print(f"  {ev.get('description')}")
    return 0


def _times(a: Any, tz: _dt.tzinfo | None, *, create: bool) -> tuple[Any, Any, str | None]:
    """--start/--end → (start, end, zone label). On create, --end defaults to
    an hour after --start (a day, for an all-day event)."""
    start: Any = UNSET
    end: Any = UNSET
    label = None
    if a.start:
        start, date_only = _when.parse_when(a.start, tz=tz, field="start")
        if date_only and not a.all_day:
            raise CliError("bad_argument",
                           "--start is a date without a time: give a time, or pass --all-day.")
        label = _when.zone_label(tz, start)
    if a.end:
        end, _ = _when.parse_when(a.end, tz=tz, end_of_day=bool(a.all_day), field="end")
        label = label or _when.zone_label(tz, end)
    if create and end is UNSET and start is not UNSET:
        end = start + (_dt.timedelta(days=1) if a.all_day else _dt.timedelta(hours=1))
    return start, end, label


def _clear(v: str | None) -> Any:
    if v is None:
        return UNSET
    return None if v == "" else v


def cmd_events_create(ctx: Ctx) -> int:
    a = ctx.args
    tz = _when.zone(a.tz)
    start, end, label = _times(a, tz, create=True)
    linked_type = linked_id = None
    if a.link:
        linked_type, linked_id = _link(a.link, "--link")
    rec = _recurrence(a)
    series = isinstance(rec, dict)
    # Alder #1: the series' own zone (RFC 5545 TZID), so it keeps its wall
    # clock across DST for EVERY reader, not only one who passes --tz.
    tzid = _when.iana_name(a.tz) if series else None
    data = ctx.client.events.create(
        a.title, start, end,
        description=a.description, all_day=bool(a.all_day), event_type=a.type or "Meeting",
        location=a.location, meeting_url=a.url, owner=a.owner,
        linked_type=linked_type, linked_id=linked_id,
        attendees=a.attendee, recurrence=rec if series else None,
        tz=tzid, dedupe=not a.force,
    )
    lines = [f"Created event: {data.get('title')} ({data.get('id')})",
             f"  when: {_when_text(data, tz)}  (times in {label})"]
    lines += _series_zone_lines(data, tzid, sent_rule=series)
    if data.get("attendees"):
        lines.append(f"  attendees: {len(data['attendees'])}")
    ctx.wrote(*lines, data=data)
    return 0


def cmd_events_update(ctx: Ctx) -> int:
    a = ctx.args
    tz = _when.zone(a.tz)
    start, end, label = _times(a, tz, create=False)
    linked: tuple[Any, Any] = (UNSET, UNSET)
    if a.link and a.unlink:
        raise CliError("bad_argument", "Pass --link or --unlink, not both.")
    if a.link:
        linked = _link(a.link, "--link")
    elif a.unlink:
        linked = (None, None)
    rec = _recurrence(a)
    series = isinstance(rec, dict)
    tzid = _when.iana_name(a.tz) if series else None
    if series and tzid:
        # Left out when unknown: the server then KEEPS a stored zone.
        rec["tzid"] = tzid
    data = ctx.client.events.update(
        a.event_id,
        title=a.title if a.title is not None else UNSET,
        start=start, end=end,
        description=_clear(a.description),
        all_day=a.all_day if a.all_day is not None else UNSET,
        event_type=a.type if a.type is not None else UNSET,
        location=_clear(a.location), meeting_url=_clear(a.url), owner=_clear(a.owner),
        linked_type=linked[0], linked_id=linked[1],
        attendees=a.attendee if a.attendee is not None else UNSET,
        recurrence=rec,
    )
    lines = [f"Updated event: {data.get('title')} ({a.event_id})",
             f"  when: {_when_text(data, tz)}" + (f"  (times in {label})" if label else "")]
    lines += _series_zone_lines(data, tzid, sent_rule=series) or ["  repeats: no"]
    ctx.wrote(*lines, data=data)
    return 0


def cmd_events_delete(ctx: Ctx) -> int:
    eid = need_uuid(ctx.args.event_id, "event id")
    data = ctx.client.events.delete(eid)
    ctx.wrote(f"Deleted event {eid} (the whole series, if it repeats)", data=data)
    return 0


# ─── wiring ─────────────────────────────────────────────────────────────


def _event_fields(s: Any, *, update: bool) -> None:
    clear = ' ("" clears)' if update else ""
    s.add_argument("--title", required=not update)
    s.add_argument("--start", required=not update,
                   help="'YYYY-MM-DD HH:MM' (local time, or --tz), ISO with an offset, or a date "
                        "with --all-day")
    s.add_argument("--end", help="same forms as --start" + ("" if update else
                   " (default: start + 1 hour, or + 1 day with --all-day)"))
    if update:
        s.add_argument("--all-day", action=argparse.BooleanOptionalAction, default=None,
                       help="mark all-day (--no-all-day to unmark)")
    else:
        s.add_argument("--all-day", action="store_true", help="an all-day event")
    s.add_argument("--type", choices=EVENT_TYPES, type=_event_type,
                   help="/".join(EVENT_TYPES) + ("" if update else " (default Meeting)"))
    s.add_argument("--location", help=f"where{clear}")
    s.add_argument("--url", help=f"meeting URL (http/https){clear}")
    s.add_argument("--description", help=f"free text{clear}")
    s.add_argument("--owner", help=f"@username, display name, or UUID (default: you){clear}")
    s.add_argument("--link", metavar="TYPE:ID",
                   help="link to an entity, e.g. project:<uuid>, contact:<uuid>")
    s.add_argument("--repeat", choices=REPEATS, help="make it a series")
    s.add_argument("--until", help="last date of the series, YYYY-MM-DD (inclusive)")
    s.add_argument("--count", type=int, help="number of occurrences (instead of --until)")
    s.add_argument("--attendee", action="append", metavar="user:NAME|contact:NAME",
                   help="add an attendee (repeatable; also email:ADDR)"
                        + ("; on update the list REPLACES the current attendees" if update else ""))
    s.add_argument("--tz", help="IANA zone for times without an offset (default: this machine's)")


def _event_type(value: str) -> str:
    canon = {t.lower(): t for t in EVENT_TYPES}.get(value.strip().lower())
    return canon or value


def register(sub: Any, add: Any) -> None:
    se = add(sub, "events", help="the mesh calendar: list, show, create, update, delete")
    ses = se.add_subparsers(dest="events_cmd", required=True, metavar="ACTION")
    s = add(ses, "list", help="events in a window (recurring ones expanded per occurrence)")
    s.add_argument("--from", dest="start", help="window start (date or time; local unless offset)")
    s.add_argument("--to", dest="end", help="window end (a date means the END of that day)")
    s.add_argument("--today", action="store_true", help="today, on this machine's clock")
    s.add_argument("--week", action="store_true", help="the next 7 days, from today 00:00")
    s.add_argument("--expand", action=argparse.BooleanOptionalAction, default=None,
                   help="one row per occurrence (default: on when a window is given)")
    s.add_argument("--linked", metavar="TYPE:ID", help="only events linked to this entity")
    s.add_argument("--owner", help="only events owned by @username, display name, or UUID")
    s.add_argument("--tz", help="IANA zone for the window and display (default: this machine's)")
    s.add_argument("--limit", type=int, default=50, help="page size (default 50)")
    s.add_argument("--offset", type=int, default=None, help="skip this many rows (paging)")
    s.set_defaults(func=cmd_events_list)
    s = add(ses, "show", help="one event with attendees and recurrence")
    s.add_argument("event_id", help="event UUID")
    s.add_argument("--tz", help="IANA zone to display times in (default: this machine's)")
    s.set_defaults(func=cmd_events_show)
    s = add(ses, "create", help="create an event (refuses a duplicate title+start unless --force)")
    _event_fields(s, update=False)
    s.add_argument("--force", action="store_true",
                   help="create even if an event with this title already starts then")
    s.set_defaults(func=cmd_events_create)
    s = add(ses, "update", help="edit an event (only the flags you pass are changed)")
    s.add_argument("event_id", help="event UUID (a series is edited as a whole)")
    _event_fields(s, update=True)
    s.add_argument("--no-repeat", action="store_true", help="stop the series (clear recurrence)")
    s.add_argument("--unlink", action="store_true", help="clear the linked entity")
    s.set_defaults(func=cmd_events_update)
    s = add(ses, "delete", help="delete an event (soft; the whole series)")
    s.add_argument("event_id", help="event UUID")
    s.set_defaults(func=cmd_events_delete)
