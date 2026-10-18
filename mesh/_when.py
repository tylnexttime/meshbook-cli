"""Turning what a person types into the aware datetimes the SDK insists on.

The SDK refuses naive datetimes (it runs everywhere and will not guess a
zone). A person at a terminal means "my clock", so the CLI decides: a time
typed without an offset is in the machine's local zone (or ``--tz``), and
every human-facing line says which zone that was.
"""
from __future__ import annotations

import datetime as _dt
import os
from pathlib import Path
from typing import Any

from ._core import CliError

_FORMS = "YYYY-MM-DD, 'YYYY-MM-DD HH:MM', or ISO 8601 with an offset (2026-10-20T09:00+11:00)"


def zone(tz_name: str | None) -> _dt.tzinfo | None:
    """``--tz Area/City`` → a tzinfo; None means the machine's local zone."""
    if not tz_name:
        return None
    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    except ImportError:  # pragma: no cover - 3.9 and older
        raise CliError("bad_argument", "--tz needs Python 3.9+ (zoneinfo).") from None
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        hint = "On Windows the IANA database ships separately:  pip install tzdata"
        raise CliError(
            "bad_argument", f"Unknown time zone {tz_name!r} (use an IANA name like Australia/Sydney).",
            hint=hint,
        ) from None


def _localise(naive: _dt.datetime, tz: _dt.tzinfo | None) -> _dt.datetime:
    if tz is None:
        return naive.astimezone()  # naive = local wall clock, DST-correct for that date
    return naive.replace(tzinfo=tz)


def midnight(day: _dt.date, tz: _dt.tzinfo | None) -> _dt.datetime:
    return _localise(_dt.datetime(day.year, day.month, day.day), tz)


def today(tz: _dt.tzinfo | None) -> _dt.date:
    return _dt.datetime.now(tz).date() if tz is not None else _dt.date.today()


def parse_when(
    text: str, *, tz: _dt.tzinfo | None = None, end_of_day: bool = False, field: str = "time"
) -> tuple[_dt.datetime, bool]:
    """Parse a user time → (aware datetime, it_was_a_date_only).

    * ``2026-10-20`` → that day's local midnight (``end_of_day``: the NEXT
      midnight, so ``--to 2026-10-23`` includes the 23rd);
    * ``2026-10-20 09:00`` / ``2026-10-20T09:00`` → local wall clock (or --tz);
    * anything with an offset or ``Z`` is kept as given.
    """
    raw = (text or "").strip()
    if not raw:
        raise CliError("bad_argument", f"--{field} is empty. Use {_FORMS}.")
    if len(raw) == 10:
        try:
            day = _dt.date.fromisoformat(raw)
        except ValueError:
            day = None
        if day is not None:
            if end_of_day:
                day = day + _dt.timedelta(days=1)
            return midnight(day, tz), True
    norm = raw
    if "T" not in norm and " " in norm:
        norm = norm.replace(" ", "T", 1)
    if norm.endswith(("Z", "z")):
        norm = norm[:-1] + "+00:00"
    try:
        dt = _dt.datetime.fromisoformat(norm)
    except ValueError:
        raise CliError("bad_argument", f"--{field} {text!r} is not a time. Use {_FORMS}.") from None
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        dt = _localise(dt, tz)
    return dt, False


def parse_date(text: str, field: str) -> _dt.date:
    try:
        return _dt.date.fromisoformat((text or "").strip())
    except ValueError:
        raise CliError("bad_argument", f"--{field} {text!r} is not a date (YYYY-MM-DD).") from None


def zone_label(tz: _dt.tzinfo | None, when: _dt.datetime | None = None) -> str:
    """``Australia/Sydney, UTC+11:00`` or ``AUS Eastern Daylight Time, UTC+11:00``."""
    if when is None:
        when = _dt.datetime.now(tz) if tz is not None else _dt.datetime.now()
    aware = when.astimezone(tz) if tz is not None else when.astimezone()
    name = getattr(tz, "key", None) or aware.tzname() or "local"
    off = aware.utcoffset() or _dt.timedelta(0)
    sign = "+" if off >= _dt.timedelta(0) else "-"
    mins = abs(int(off.total_seconds())) // 60
    return f"{name}, UTC{sign}{mins // 60:02d}:{mins % 60:02d}"


def iana_name(tz_arg: str | None) -> str | None:
    """The IANA name to send as the server's ``tz`` (the wall clock recurrences
    repeat in): ``--tz``, else ``$TZ`` when it is an Area/City name, else the
    ``/etc/localtime`` link target on POSIX. None when unknowable (Windows
    without --tz): the server then expands on UTC, and the CLI says so."""
    if tz_arg:
        return tz_arg
    env = (os.environ.get("TZ") or "").strip().lstrip(":")
    if "/" in env and not env.startswith("/"):
        return env
    link = Path("/etc/localtime")
    try:
        if link.is_symlink():
            target = str(link.resolve())
            if "zoneinfo/" in target:
                return target.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    return None


def show(value: Any, tz: _dt.tzinfo | None, fmt: str = "%a %Y-%m-%d %H:%M") -> str:
    """A server timestamp (UTC ISO) shown on the display clock."""
    if not value:
        return ""
    text = str(value)
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        dt = _dt.datetime.fromisoformat(text)
    except ValueError:
        return str(value)
    if dt.tzinfo is None:
        return dt.strftime(fmt)
    return (dt.astimezone(tz) if tz is not None else dt.astimezone()).strftime(fmt)


__all__ = [
    "iana_name",
    "midnight",
    "parse_date",
    "parse_when",
    "show",
    "today",
    "zone",
    "zone_label",
]
